"""Background orchestration of a transcription job.

Pipeline:  ffmpeg normalize -> DiarService upload -> stream status ->
store result -> (optional) DeepSeek report -> generate PDFs -> done.
"""
import asyncio
import logging
import subprocess
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import AsyncSessionLocal
from ..models import Job, JobMode, JobStatus
from . import diar_client, progress_hub
from .deepseek import report_generator
from .pdf import html_to_pdf, text_to_pdf

logger = logging.getLogger("transcribe.processor")


def _user_dir(user_id: uuid.UUID) -> Path:
    p = settings.storage_path / "users" / str(user_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def normalize_to_wav(input_path: Path, out_dir: Path) -> Path:
    """Convert any audio/video to 16 kHz mono PCM WAV (same as the bot did)."""
    out_wav = out_dir / f"{input_path.stem}_16k.wav"
    cmd = [
        settings.ffmpeg_bin, "-y",
        "-i", str(input_path),
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        str(out_wav),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
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
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, job_id)
        if job is None:
            logger.error("process_job: job %s not found", job_id)
            return
        try:
            await _run(db, job)
        except Exception as e:  # noqa: BLE001
            logger.exception("Job %s failed", job_id)
            await _set(
                db, job, status=JobStatus.failed,
                error=f"Внутренняя ошибка обработки: {e}",
            )


async def _run(db: AsyncSession, job: Job) -> None:
    src = Path(job.stored_path)
    if not src.exists():
        await _set(db, job, status=JobStatus.failed, error="Исходный файл не найден")
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
        await _set(db, job, status=JobStatus.failed, error=str(e))
        return
    except Exception as e:  # noqa: BLE001
        await _set(db, job, status=JobStatus.failed,
                   error=f"Сервис распознавания недоступен: {e}")
        return
    await _set(db, job, diar_task_id=task_id, stage_message="В очереди на распознавание…")

    # 3) stream status
    result_text: str | None = None
    result_segments: list | None = None
    async for upd in diar_client.stream_status(task_id):
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

    await _set(db, job, progress=90, stage_message="Сохранение результата…",
               result_text=result_text, result_segments=result_segments)

    # 4) save transcript txt + pdf
    base = work_dir / f"{job.id}"
    await asyncio.to_thread(
        text_to_pdf, result_text, base.with_name(f"{job.id}_transcript.pdf")
    )
    transcript_pdf = base.with_name(f"{job.id}_transcript.pdf")
    if transcript_pdf.exists():
        job.transcript_pdf_path = str(transcript_pdf)

    # 5) mode-specific report via DeepSeek
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
                    text_to_pdf, summary, pdf_path, "AI-выжимка"
                )
                if pdf_path.exists():
                    job.report_pdf_path = str(pdf_path)

    await _set(db, job, status=JobStatus.completed, progress=100,
               stage_message="Готово", error=None)
