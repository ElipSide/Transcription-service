"""Background orchestration of a transcription job.

Pipeline:  ffmpeg normalize -> DiarService upload -> stream status ->
store result -> (optional) DeepSeek report -> generate PDFs -> done.
"""
import asyncio
import logging
import subprocess
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import AsyncSessionLocal
from ..models import Job, JobMode, JobStatus
from . import diar_client, progress_hub
from .deepseek import report_generator
from .pdf import html_to_pdf, markdown_to_pdf, segments_to_pdf, text_to_pdf

logger = logging.getLogger("transcribe.processor")

# Jobs currently being processed in this event loop. Guards against the same
# job being dispatched twice (e.g. by upload_complete and the retry loop).
_inflight: set[uuid.UUID] = set()

# Bounds heavy work (ffmpeg + recognition). Uploading a batch of files starts a
# task per file; without this they'd all normalize at once. The rest simply wait
# their turn, which is what the user sees as "В очереди".
_slots = asyncio.Semaphore(max(1, settings.max_concurrent_jobs))


def _user_dir(user_id: uuid.UUID) -> Path:
    p = settings.storage_path / "users" / str(user_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def normalize_to_wav(input_path: Path, out_dir: Path) -> Path:
    """Convert any audio/video to 16 kHz mono PCM WAV.

    Tolerant to corrupt input (e.g. phone recordings with damaged AAC frames):
    input-side flags skip broken packets instead of aborting; if ffmpeg still
    exits non-zero but wrote a usable WAV, we keep the partial audio rather than
    failing the whole job.
    """
    out_wav = out_dir / f"{input_path.stem}_16k.wav"
    cmd = [
        settings.ffmpeg_bin, "-y",
        "-err_detect", "ignore_err",     # don't abort on decode errors
        "-fflags", "+discardcorrupt",    # drop corrupt packets
        "-i", str(input_path),
        "-vn", "-sn", "-dn",             # audio only (ignore video/subs/data streams)
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        str(out_wav),
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        # Best-effort: corrupt frames may still have yielded most of the audio.
        # A valid WAV header is 44 bytes, so anything meaningfully larger is usable.
        if out_wav.exists() and out_wav.stat().st_size > 1024:
            logger.warning(
                "ffmpeg exited %d for %s but produced %d bytes — using partial audio",
                proc.returncode, input_path.name, out_wav.stat().st_size,
            )
            return out_wav
        raise subprocess.CalledProcessError(
            proc.returncode, cmd, proc.stdout, proc.stderr
        )
    return out_wav


async def _publish(db: AsyncSession, job: Job) -> None:
    await db.commit()
    progress_hub.publish(
        str(job.id),
        {
            "status": job.status.value,
            "progress": job.progress,
            "stage_message": job.stage_message,
            "error": job.error,
        },
    )


async def _set(db: AsyncSession, job: Job, **fields) -> None:
    for k, v in fields.items():
        setattr(job, k, v)
    await _publish(db, job)


async def process_job(job_id: uuid.UUID) -> None:
    # Guard set synchronously (before any await) so a concurrent dispatch of the
    # same job returns immediately instead of double-processing.
    if job_id in _inflight:
        return
    _inflight.add(job_id)
    try:
        async with AsyncSessionLocal() as db:
            job = await db.get(Job, job_id)
            if job is None:
                logger.error("process_job: job %s not found", job_id)
                return
            try:
                if _slots.locked():
                    await _set(db, job, status=JobStatus.queued, progress=0,
                               stage_message="В очереди на обработку…")
                async with _slots:
                    await _run(db, job)
            except Exception as e:  # noqa: BLE001
                logger.exception("Job %s failed", job_id)
                await _set(
                    db, job, status=JobStatus.failed,
                    error=f"Внутренняя ошибка обработки: {e}",
                )
    finally:
        _inflight.discard(job_id)


async def _run(db: AsyncSession, job: Job) -> None:
    src = Path(job.stored_path)
    if not src.exists():
        await _set(db, job, status=JobStatus.failed, error="Исходный файл не найден")
        return

    # 0) DiarService reachable? If not, park the job as "waiting" (no ffmpeg work
    #    wasted) — the reconciler loop re-dispatches it once the service is back.
    if not await diar_client.refresh_health():
        logger.info("DiarService unavailable -> job %s parked as waiting", job.id)
        await _set(
            db, job, status=JobStatus.waiting, progress=0, error=None,
            stage_message="Сервис распознавания недоступен — задача ждёт в очереди "
                          "и начнётся автоматически, как только сервис вернётся.",
        )
        return

    work_dir = _user_dir(job.user_id)

    # 1) normalize
    await _set(db, job, status=JobStatus.processing, progress=2,
               stage_message="Подготовка аудио…")
    try:
        wav_path = await asyncio.to_thread(normalize_to_wav, src, work_dir)
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode("utf-8", "ignore")[-500:]
        await _set(db, job, status=JobStatus.failed,
                   error=f"Не удалось обработать файл (ffmpeg): {stderr}")
        return

    # 2) upload to DiarService
    await _set(db, job, progress=5, stage_message="Отправка в сервис распознавания…")
    try:
        task_id = await diar_client.upload(wav_path)
    except diar_client.DiarUploadError as e:
        # Meaningful HTTP rejection (e.g. 413 too large) -> a real failure.
        await _set(db, job, status=JobStatus.failed, error=str(e))
        return
    except Exception as e:  # noqa: BLE001 — 5xx/gateway (DiarUnavailableError) or transport drop
        logger.warning("Upload to DiarService unavailable, re-queuing job %s: %s", job.id, e)
        diar_client._available = False  # reflect immediately in the status banner
        await _set(
            db, job, status=JobStatus.waiting, progress=0, error=None,
            stage_message="Сервис распознавания недоступен — задача ждёт в очереди "
                          "и начнётся автоматически, как только сервис вернётся.",
        )
        return
    # The normalized copy exists only to be uploaded; drop it now instead of
    # letting a 16 kHz WAV pile up per job. Re-runs re-create it from the source.
    try:
        wav_path.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("Could not remove temp wav %s: %s", wav_path, e)

    await _set(db, job, diar_task_id=task_id, stage_message="В очереди на распознавание…")

    # 3) stream status
    result_text: str | None = None
    result_segments: list | None = None
    diarization_error: str | None = None
    async for upd in diar_client.stream_status(task_id):
        if upd.diarization_error:
            diarization_error = upd.diarization_error
        if upd.status == "failed":
            await _set(db, job, status=JobStatus.failed,
                       error=upd.error or "Ошибка распознавания")
            return
        if upd.status == "completed":
            result_text = upd.result_text or ""
            result_segments = upd.result_segments
            break
        # processing / queued
        job.progress = max(job.progress, min(90, 5 + int(upd.progress * 0.85)))
        job.stage_message = upd.message or "Распознавание…"
        await _publish(db, job)

    if result_text is None:
        await _set(db, job, status=JobStatus.failed, error="Пустой результат распознавания")
        return

    note: str | None = None
    if diarization_error:
        note = (
            "Разделение по спикерам недоступно — все реплики отнесены к одному "
            f"говорящему. Техническая причина: {diarization_error}"
        )
        logger.warning("Job %s diarization degraded: %s", job.id, diarization_error)

    await _set(db, job, progress=90, stage_message="Сохранение результата…",
               result_text=result_text, result_segments=result_segments,
               diarization_note=note)

    await _finalize(db, job, result_text, result_segments, work_dir)


async def _finalize(
    db: AsyncSession,
    job: Job,
    result_text: str,
    result_segments: list | None,
    work_dir: Path,
) -> None:
    """Transcript PDF + mode-specific AI report + mark completed.

    Shared by a fresh run and by a re-run in a different mode (which reuses an
    existing transcript and therefore needs no audio / DiarService).
    """
    # 1) transcript pdf — styled like the web (speaker colors / dialogue)
    #    when we have diarized segments, plain text as a fallback.
    base = work_dir / f"{job.id}"
    transcript_pdf = base.with_name(f"{job.id}_transcript.pdf")
    if result_segments:
        await asyncio.to_thread(segments_to_pdf, result_segments, transcript_pdf)
    else:
        await asyncio.to_thread(text_to_pdf, result_text, transcript_pdf)
    if transcript_pdf.exists():
        job.transcript_pdf_path = str(transcript_pdf)

    # 2) mode-specific report via DeepSeek
    if job.mode in (JobMode.report, JobMode.ai_extract) and settings.deepseek_api_key:
        await _set(db, job, progress=94, stage_message="Формирование отчёта (AI)…")
        if job.mode == JobMode.report:
            html = await report_generator.business_report_html(result_text)
            if html:
                job.report_html = html
                pdf_path = base.with_name(f"{job.id}_report.pdf")
                await asyncio.to_thread(html_to_pdf, html, pdf_path)
                if pdf_path.exists():
                    job.report_pdf_path = str(pdf_path)
        else:  # ai_extract
            summary = await report_generator.ai_extract(result_text)
            if summary:
                job.report_html = summary
                pdf_path = base.with_name(f"{job.id}_extract.pdf")
                await asyncio.to_thread(
                    markdown_to_pdf, summary, pdf_path, "AI-выжимка"
                )
                if pdf_path.exists():
                    job.report_pdf_path = str(pdf_path)

    await _set(db, job, status=JobStatus.completed, progress=100,
               stage_message="Готово", error=None)


async def process_rerun(job_id: uuid.UUID) -> None:
    """Re-process a job in a different mode, reusing an existing transcript.

    No ffmpeg, no DiarService, no GPU — only the AI stage and PDFs.
    """
    if job_id in _inflight:
        return
    _inflight.add(job_id)
    try:
        async with AsyncSessionLocal() as db:
            job = await db.get(Job, job_id)
            if job is None:
                logger.error("process_rerun: job %s not found", job_id)
                return
            try:
                if not job.result_text:
                    await _set(db, job, status=JobStatus.failed,
                               error="Нет исходной транскрипции для повторной обработки")
                    return
                await _set(db, job, status=JobStatus.processing, progress=60,
                           stage_message="Повторная обработка готовой транскрипции…")
                await _finalize(
                    db, job, job.result_text, job.result_segments,
                    _user_dir(job.user_id),
                )
            except Exception as e:  # noqa: BLE001
                logger.exception("Rerun %s failed", job_id)
                await _set(
                    db, job, status=JobStatus.failed,
                    error=f"Внутренняя ошибка обработки: {e}",
                )
    finally:
        _inflight.discard(job_id)


# ---------------------------------------------------------------------------
# Reconciler: retries jobs that are waiting on DiarService, and recovers jobs
# orphaned by a backend restart. Started once from the app lifespan.
# ---------------------------------------------------------------------------
async def _dispatch_pending(diar_ok: bool) -> int:
    """Dispatch pending jobs that aren't already in flight.

    Full jobs (with audio) need DiarService; re-runs (transcript already there,
    no stored audio) don't, so they're recovered regardless.
    """
    async with AsyncSessionLocal() as db:
        rows = await db.scalars(
            select(Job)
            .where(Job.status.in_((JobStatus.waiting, JobStatus.queued)))
            .order_by(Job.created_at)
        )
        pending = [(j.id, bool(j.stored_path), bool(j.result_text)) for j in rows]

    started = 0
    for job_id, has_audio, has_text in pending:
        if job_id in _inflight:
            continue
        if has_audio:
            if diar_ok:
                asyncio.create_task(process_job(job_id))
                started += 1
        elif has_text:
            asyncio.create_task(process_rerun(job_id))
            started += 1
    return started


async def retry_waiting_loop() -> None:
    """Forever: refresh DiarService health; when it's up, flush the wait queue."""
    interval = max(5, settings.diar_health_interval)
    logger.info("DiarService reconciler started (interval=%ss)", interval)
    while True:
        try:
            await asyncio.sleep(interval)
            diar_ok = await diar_client.refresh_health()
            started = await _dispatch_pending(diar_ok)
            if started:
                logger.info("Dispatched %d pending job(s) (diar_ok=%s)", started, diar_ok)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — never let the loop die
            logger.exception("retry_waiting_loop iteration failed")
