import asyncio
import shutil
import uuid
from pathlib import Path

import jwt
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import AsyncSessionLocal, get_db
from ..deps import get_current_user
from ..models import Job, JobMode, JobStatus, User
from ..schemas import (
    JobDetail,
    JobListItem,
    UploadCompleteOut,
    UploadInit,
    UploadInitOut,
)
from ..security import ACCESS, decode_token
from ..services import progress_hub
from ..services.processor import process_job

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB


def _chunk_dir(upload_id: str) -> Path:
    p = settings.chunk_path / upload_id
    p.mkdir(parents=True, exist_ok=True)
    return p


async def _get_owned_job(db: AsyncSession, job_id: uuid.UUID, user: User) -> Job:
    job = await db.get(Job, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return job


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


# ---------- Listing / detail ----------
@router.get("", response_model=list[JobListItem])
async def list_jobs(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Job]:
    rows = await db.scalars(
        select(Job)
        .where(Job.user_id == user.id, Job.status != JobStatus.uploading)
        .order_by(Job.created_at.desc())
    )
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
    base = Path(job.original_filename).stem
    return FileResponse(path, filename=f"{base}_{kind}.pdf", media_type="application/pdf")


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
