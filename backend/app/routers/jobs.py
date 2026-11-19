import asyncio
import io
import re
import shutil
import uuid
import zipfile
from pathlib import Path
from urllib.parse import quote

import jwt
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import AsyncSessionLocal, get_db
from ..deps import get_current_user
from ..models import Job, JobMode, JobStatus, User
from ..schemas import (
    ArchiveRequest,
    JobDetail,
    JobListItem,
    RerunRequest,
    UploadCompleteOut,
    UploadInit,
    UploadInitOut,
)
from ..security import ACCESS, decode_token
from ..services import progress_hub
from ..services.processor import process_job, process_rerun

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB


def _chunk_dir(upload_id: str) -> Path:
    p = settings.chunk_path / upload_id
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------- Result file naming ----------
def _safe_stem(name: str) -> str:
    """Audio file name without extension, safe to use as a file name."""
    stem = Path(name).stem
    return re.sub(r'[\\/:*?"<>|]', "_", stem).strip() or "file"


def _result_filename(job: Job, kind: str) -> str:
    """'Транскрибация - meeting.pdf' / 'Отчёт - meeting.pdf' / 'Выдержка - meeting.pdf'.

    The 'report' file holds the business report or the AI summary depending on
    the job mode, so the prefix follows the mode.
    """
    if kind == "transcript":
        prefix = "Транскрибация"
    elif job.mode == JobMode.ai_extract:
        prefix = "Выдержка"
    else:
        prefix = "Отчёт"
    return f"{prefix} - {_safe_stem(job.original_filename)}.pdf"


def _unique_name(name: str, used: set[str]) -> str:
    """Keep archive entries distinct when two jobs share an audio file name."""
    if name not in used:
        used.add(name)
        return name
    stem, _, ext = name.rpartition(".")
    i = 2
    while f"{stem} ({i}).{ext}" in used:
        i += 1
    out = f"{stem} ({i}).{ext}"
    used.add(out)
    return out


def _content_disposition(filename: str) -> dict[str, str]:
    """RFC 5987 header so Cyrillic names survive."""
    return {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}


async def _get_owned_job(db: AsyncSession, job_id: uuid.UUID, user: User) -> Job:
    job = await db.get(Job, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return job


# ---------- Upload ----------
@router.post("/upload", response_model=UploadCompleteOut)
async def upload_direct(
    file: UploadFile = File(...),
    mode: JobMode = Form(JobMode.transcription),
    batch_id: uuid.UUID | None = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UploadCompleteOut:
    """Single-request upload: the whole file as one multipart form field.

    The convenient path for machine integrations and modest files; anything
    large should use the chunked flow below — it is also the only way past
    proxy body-size limits this endpoint is subject to.
    """
    user_dir = settings.storage_path / "users" / str(user.id)
    user_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = user_dir / f".{uuid.uuid4().hex}.part"

    # Stream to a temp file first: an interrupted body or a size violation must
    # not leave a job row (or a half-file under its final name) behind.
    size = 0
    try:
        with open(tmp_path, "wb") as out:
            while data := await file.read(1024 * 1024):
                size += len(data)
                if size > settings.max_file_size:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"Файл слишком большой "
                            f"(макс. {settings.max_file_size // (1024*1024)} МБ)"
                        ),
                    )
                out.write(data)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    if size == 0:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Получен пустой файл")

    name = (file.filename or "").strip()[:512] or "file"
    job_id = uuid.uuid4()
    final_path = user_dir / f"{job_id}{Path(name).suffix}"
    tmp_path.replace(final_path)

    job = Job(
        id=job_id,
        user_id=user.id,
        original_filename=name,
        file_size=size,
        stored_path=str(final_path),
        mode=mode,
        batch_id=batch_id,
        status=JobStatus.queued,
        stage_message="В очереди…",
    )
    db.add(job)
    await db.commit()

    asyncio.create_task(process_job(job_id))
    return UploadCompleteOut(job_id=job_id)


# ---------- Chunked upload ----------
@router.post("/upload/init", response_model=UploadInitOut)
async def upload_init(
    payload: UploadInit,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UploadInitOut:
    if payload.file_size > settings.max_file_size:
        raise HTTPException(
            status_code=413,
            detail=f"Файл слишком большой (макс. {settings.max_file_size // (1024*1024)} МБ)",
        )
    job = Job(
        user_id=user.id,
        original_filename=payload.filename[:512],
        file_size=payload.file_size,
        mode=payload.mode,
        batch_id=payload.batch_id,
        status=JobStatus.uploading,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    _chunk_dir(str(job.id))
    return UploadInitOut(upload_id=str(job.id), chunk_size=CHUNK_SIZE)


@router.post("/upload/{upload_id}/chunk")
async def upload_chunk(
    upload_id: uuid.UUID,
    index: int = Query(ge=0),
    chunk: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    job = await _get_owned_job(db, upload_id, user)
    if job.status != JobStatus.uploading:
        raise HTTPException(status_code=409, detail="Загрузка уже завершена")
    part_path = _chunk_dir(str(upload_id)) / f"{index:06d}.part"
    with open(part_path, "wb") as f:
        while data := await chunk.read(1024 * 1024):
            f.write(data)
    return {"ok": True, "index": index}


@router.post("/upload/{upload_id}/complete", response_model=UploadCompleteOut)
async def upload_complete(
    upload_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UploadCompleteOut:
    job = await _get_owned_job(db, upload_id, user)
    if job.status != JobStatus.uploading:
        raise HTTPException(status_code=409, detail="Загрузка уже завершена")

    cdir = _chunk_dir(str(upload_id))
    parts = sorted(cdir.glob("*.part"))
    if not parts:
        raise HTTPException(status_code=400, detail="Не получено ни одного фрагмента файла")

    user_dir = settings.storage_path / "users" / str(user.id)
    user_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(job.original_filename).suffix
    final_path = user_dir / f"{job.id}{suffix}"

    with open(final_path, "wb") as out:
        for part in parts:
            with open(part, "rb") as pf:
                shutil.copyfileobj(pf, out)
    shutil.rmtree(cdir, ignore_errors=True)

    job.stored_path = str(final_path)
    job.file_size = final_path.stat().st_size
    job.status = JobStatus.queued
    job.stage_message = "В очереди…"
    await db.commit()

    asyncio.create_task(process_job(job.id))
    return UploadCompleteOut(job_id=job.id)


@router.post("/{job_id}/rerun", response_model=UploadCompleteOut)
async def rerun_job(
    job_id: uuid.UUID,
    payload: RerunRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UploadCompleteOut:
    """Process an already-recognized file in another mode.

    Creates a new job that reuses the existing transcript, so recognition is not
    repeated — only the AI stage runs. The derived job intentionally has no
    stored_path: it never needs the audio, and that keeps deleting either job
    from removing a file the other one relies on.
    """
    src = await _get_owned_job(db, job_id, user)
    if src.status != JobStatus.completed or not src.result_text:
        raise HTTPException(
            status_code=409,
            detail="Повторная обработка доступна только для завершённых задач с готовой транскрипцией",
        )

    job = Job(
        user_id=user.id,
        original_filename=src.original_filename,
        file_size=src.file_size,
        mode=payload.mode,
        status=JobStatus.queued,
        stage_message="В очереди…",
        result_text=src.result_text,
        result_segments=src.result_segments,
        diarization_note=src.diarization_note,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    asyncio.create_task(process_rerun(job.id))
    return UploadCompleteOut(job_id=job.id)


# ---------- Listing / detail ----------
@router.get("", response_model=list[JobListItem])
async def list_jobs(
    batch_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Job]:
    """List the caller's jobs (lightweight items, no result_text/segments).

    Pass ?batch_id=<uuid> to get just one upload group — one request polls a
    whole batch instead of hitting GET /jobs/{id} per file.
    """
    stmt = select(Job).where(Job.user_id == user.id, Job.status != JobStatus.uploading)
    if batch_id is not None:
        stmt = stmt.where(Job.batch_id == batch_id)
    rows = await db.scalars(stmt.order_by(Job.created_at.desc()))
    return list(rows)


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(
    job_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JobDetail:
    job = await _get_owned_job(db, job_id, user)
    detail = JobDetail.model_validate(job)
    detail.has_report_pdf = bool(job.report_pdf_path and Path(job.report_pdf_path).exists())
    detail.has_transcript_pdf = bool(
        job.transcript_pdf_path and Path(job.transcript_pdf_path).exists()
    )
    return detail


@router.delete("/{job_id}", status_code=204)
async def delete_job(
    job_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    job = await _get_owned_job(db, job_id, user)
    for p in (job.stored_path, job.report_pdf_path, job.transcript_pdf_path):
        if p and Path(p).exists():
            Path(p).unlink(missing_ok=True)
    await db.delete(job)
    await db.commit()


# ---------- Downloads ----------
@router.get("/{job_id}/download/{kind}")
async def download(
    job_id: uuid.UUID,
    kind: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    job = await _get_owned_job(db, job_id, user)
    mapping = {
        "transcript": job.transcript_pdf_path,
        "report": job.report_pdf_path,
    }
    path = mapping.get(kind)
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="Файл недоступен")
    return FileResponse(
        path,
        filename=_result_filename(job, kind),
        media_type="application/pdf",
    )


def _build_zip(entries: list[tuple[str, str]]) -> bytes:
    """entries: [(source_path, name_in_archive)]. Runs in a thread — file IO."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for src, arcname in entries:
            zf.write(src, arcname=arcname)
    return buf.getvalue()


@router.post("/archive")
async def download_archive(
    payload: ArchiveRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Download results of several jobs as one ZIP (batch upload -> batch download)."""
    rows = await db.scalars(
        select(Job)
        .where(Job.id.in_(payload.job_ids), Job.user_id == user.id)
        .order_by(Job.created_at)
    )
    jobs = list(rows)
    if not jobs:
        raise HTTPException(status_code=404, detail="Задачи не найдены")

    entries: list[tuple[str, str]] = []
    used: set[str] = set()
    for job in jobs:
        wanted: list[tuple[str, str | None]] = []
        if payload.kind in ("all", "transcript"):
            wanted.append(("transcript", job.transcript_pdf_path))
        if payload.kind in ("all", "report"):
            wanted.append(("report", job.report_pdf_path))
        for kind, path in wanted:
            if path and Path(path).exists():
                entries.append((path, _unique_name(_result_filename(job, kind), used)))

    if not entries:
        raise HTTPException(status_code=404, detail="Нет готовых файлов для скачивания")

    data = await asyncio.to_thread(_build_zip, entries)
    label = {"transcript": "Транскрибации", "report": "Отчёты"}.get(payload.kind, "Разбор")
    return Response(
        content=data,
        media_type="application/zip",
        headers=_content_disposition(f"{label} ({len(entries)}).zip"),
    )


# ---------- Live progress (browser-facing WebSocket) ----------
async def _ws_authenticate(token: str | None) -> uuid.UUID | None:
    if not token:
        return None
    try:
        payload = decode_token(token, expected_type=ACCESS)
        return uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


@router.websocket("/{job_id}/ws")
async def job_ws(websocket: WebSocket, job_id: uuid.UUID, token: str | None = Query(None)):
    user_id = await _ws_authenticate(token)
    if user_id is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # verify ownership + send current snapshot
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, job_id)
        if job is None or job.user_id != user_id:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await websocket.accept()
        await websocket.send_json({
            "status": job.status.value,
            "progress": job.progress,
            "stage_message": job.stage_message,
            "error": job.error,
        })
        terminal = job.status in (JobStatus.completed, JobStatus.failed)

    if terminal:
        await websocket.close()
        return

    q = progress_hub.subscribe(str(job_id))
    try:
        while True:
            payload = await q.get()
            await websocket.send_json(payload)
            if payload.get("status") in ("completed", "failed"):
                break
    except WebSocketDisconnect:
        pass
    finally:
        progress_hub.unsubscribe(str(job_id), q)
        try:
            await websocket.close()
        except RuntimeError:
            pass
