"""Storage retention.

Source audio is large (up to 2 GB per file) and useless once a job is done, so
it is removed after `audio_retention_days`. Everything the user actually needs —
transcript text/segments in the DB and the generated PDFs (транскрибация,
отчёт, выжимка) — is kept indefinitely.

Audio is only ever dropped for jobs in a terminal state: a job still queued or
waiting on DiarService needs its source file to be processed later.
"""
import asyncio
import logging
import shutil
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from sqlalchemy.orm import selectinload

from ..config import settings
from ..database import AsyncSessionLocal
from ..models import Job, JobStatus, User

logger = logging.getLogger("transcribe.cleanup")

_TERMINAL = (JobStatus.completed, JobStatus.failed)


def _unlink(path: Path) -> int:
    """Delete a file, returning the bytes reclaimed."""
    try:
        size = path.stat().st_size
        path.unlink()
        return size
    except FileNotFoundError:
        return 0
    except OSError as e:
        logger.warning("Could not delete %s: %s", path, e)
        return 0


def _fmt_mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} МБ"


async def purge_old_audio() -> int:
    """Drop source audio (and any stale normalized wav) for old finished jobs."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.audio_retention_days)
    freed = 0

    async with AsyncSessionLocal() as db:
        rows = await db.scalars(
            select(Job).where(
                Job.status.in_(_TERMINAL),
                Job.updated_at < cutoff,
                Job.stored_path.is_not(None),
            )
        )
        jobs = list(rows)
        for job in jobs:
            stored = Path(job.stored_path)
            freed += await asyncio.to_thread(_unlink, stored)
            # ffmpeg's 16 kHz copy, normally already removed after upload
            freed += await asyncio.to_thread(
                _unlink, stored.with_name(f"{job.id}_16k.wav")
            )
            # Results stay; only the pointer to the (now gone) audio is cleared.
            job.stored_path = None
        if jobs:
            await db.commit()
            logger.info(
                "Purged audio of %d job(s), freed %s", len(jobs), _fmt_mb(freed)
            )
    return freed


async def purge_abandoned_uploads() -> int:
    """Remove chunk dirs (and their job rows) from uploads never completed."""
    chunk_root = settings.chunk_path
    max_age = settings.chunk_retention_hours * 3600
    now = time.time()
    removed: list[str] = []
    freed = 0

    def _scan() -> list[Path]:
        try:
            return [p for p in chunk_root.iterdir() if p.is_dir()]
        except OSError:
            return []

    for d in await asyncio.to_thread(_scan):
        try:
            if now - d.stat().st_mtime < max_age:
                continue
            freed += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            await asyncio.to_thread(shutil.rmtree, d, True)
            removed.append(d.name)
        except OSError as e:
            logger.warning("Could not clean chunk dir %s: %s", d, e)

    # Temp files of single-request uploads interrupted by a hard kill (the
    # endpoint itself deletes them on any handled failure).
    def _scan_parts() -> list[Path]:
        try:
            return [
                p
                for udir in (settings.storage_path / "users").iterdir()
                if udir.is_dir()
                for p in udir.glob(".*.part")
                if now - p.stat().st_mtime > max_age
            ]
        except OSError:
            return []

    for f in await asyncio.to_thread(_scan_parts):
        freed += await asyncio.to_thread(_unlink, f)

    if removed:
        # These jobs never got a file, carry no results and are hidden from the
        # UI (list_jobs filters out `uploading`) — pure leftovers.
        ids: list[uuid.UUID] = []
        for name in removed:
            try:
                ids.append(uuid.UUID(name))
            except ValueError:
                continue
        if ids:
            async with AsyncSessionLocal() as db:
                rows = await db.scalars(
                    select(Job).where(
                        Job.id.in_(ids), Job.status == JobStatus.uploading
                    )
                )
                stale = list(rows)
                for job in stale:
                    await db.delete(job)
                if stale:
                    await db.commit()
        logger.info(
            "Removed %d abandoned upload(s), freed %s", len(removed), _fmt_mb(freed)
        )
    return freed


async def purge_guest_sessions() -> int:
    """Delete expired anonymous sessions together with everything they made."""
    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=settings.guest_retention_hours
    )
    freed = 0

    async with AsyncSessionLocal() as db:
        rows = await db.scalars(
            select(User)
            .where(User.is_guest.is_(True), User.created_at < cutoff)
            .options(selectinload(User.jobs))
        )
        guests = list(rows)
        for user in guests:
            # Job rows go via cascade; their files have to be removed by hand.
            for job in user.jobs:
                for p in (
                    job.stored_path,
                    job.transcript_pdf_path,
                    job.report_pdf_path,
                ):
                    if p:
                        freed += await asyncio.to_thread(_unlink, Path(p))
            await db.delete(user)
        if guests:
            await db.commit()
            logger.info(
                "Purged %d guest session(s), freed %s", len(guests), _fmt_mb(freed)
            )
    return freed


async def run_once() -> None:
    await purge_old_audio()
    await purge_abandoned_uploads()
    await purge_guest_sessions()


async def cleanup_loop() -> None:
    """Forever: apply retention on a fixed interval."""
    interval = max(5, settings.cleanup_interval_minutes) * 60
    logger.info(
        "Cleanup started (audio kept %d day(s), interval %d min)",
        settings.audio_retention_days,
        interval // 60,
    )
    while True:
        try:
            await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — never let the janitor die
            logger.exception("cleanup iteration failed")
        await asyncio.sleep(interval)
