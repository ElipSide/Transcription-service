"""Client for the DiarService recognition/diarization backend.

Mirrors how the Telegram bot talked to it:
  POST {base}/upload                  -> {"task_id": ..., "ws": ...}
  WS   {ws}/ws/status/{task_id}       -> {status, progress, message, result_text, result_segments}
"""
import asyncio
import json
import logging
import ssl
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import websockets

from ..config import settings

logger = logging.getLogger("transcribe.diar")


def _ws_ssl_context():
    """SSL context for wss:// — relaxed when the cert is self-signed."""
    if not settings.diar_ws_url.startswith("wss"):
        return None
    ctx = ssl.create_default_context()
    if not settings.diar_verify_ssl:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


class DiarUploadError(Exception):
    """Raised when DiarService rejects an upload with a meaningful HTTP error."""


# ---- Availability tracking ---------------------------------------------------
# Cached so the browser-facing status endpoint doesn't hit DiarService on every
# poll; the reconciler loop refreshes it on a fixed interval.
_available: bool = True


async def health(timeout: float | None = None) -> bool:
    """True if DiarService answers at all (any non-5xx HTTP response).

    A connection refusal / timeout (service down or unreachable) -> False.
    """
    url = settings.diar_base_url
    to = timeout if timeout is not None else settings.diar_health_timeout
    try:
        async with httpx.AsyncClient(timeout=to, verify=settings.diar_verify_ssl) as client:
            resp = await client.get(url)
        return resp.status_code < 500
    except Exception:  # noqa: BLE001 — any transport error means "down"
        return False


async def refresh_health() -> bool:
    """Ping DiarService and update the cached availability flag."""
    global _available
    ok = await health()
    if ok != _available:
        logger.info("DiarService availability changed: %s -> %s", _available, ok)
    _available = ok
    return ok


def is_available() -> bool:
    """Last known availability (updated by the reconciler loop)."""
    return _available


@dataclass
class DiarUpdate:
    status: str = "processing"
    progress: int = 0
    message: str = ""
    result_text: str | None = None
    result_segments: list | None = None
    error: str | None = None
    diarization_error: str | None = None
    raw: dict = field(default_factory=dict)


async def upload(file_path: Path) -> str:
    """Upload a file and return the DiarService task_id."""
    url = f"{settings.diar_base_url}/upload"
    timeout = httpx.Timeout(settings.diar_upload_timeout, connect=60.0)
    async with httpx.AsyncClient(timeout=timeout, verify=settings.diar_verify_ssl) as client:
        with open(file_path, "rb") as f:
            files = {"file": (file_path.name, f, "application/octet-stream")}
            resp = await client.post(url, files=files)

    if resp.status_code == 413:
        raise DiarUploadError(
            "Файл слишком большой для сервиса распознавания. "
            "Увеличьте client_max_body_size в nginx DiarService (на стороне 109.202.9.107)."
        )
    if resp.status_code >= 400:
        body = resp.text[:200].replace("\n", " ")
        raise DiarUploadError(
            f"Сервис распознавания отклонил файл (HTTP {resp.status_code}): {body}"
        )

    data = resp.json()
    task_id = data.get("task_id")
    if not task_id:
        raise DiarUploadError(f"DiarService не вернул task_id: {data}")
    return task_id


async def stream_status(task_id: str, max_retries: int = 10):
    """Async generator yielding DiarUpdate until completed/failed.

    Reconnects on transient WebSocket errors (matches the bot's retry logic).
    """
    ws_url = f"{settings.diar_ws_url}/ws/status/{task_id}"
    ssl_ctx = _ws_ssl_context()
    retry = 0
    while retry < max_retries:
        try:
            async with websockets.connect(ws_url, max_size=None, ssl=ssl_ctx) as ws:
                async for raw_msg in ws:
                    try:
                        data = json.loads(raw_msg)
                    except json.JSONDecodeError:
                        continue
                    update = DiarUpdate(
                        status=data.get("status", "processing"),
                        progress=int(data.get("progress", 0) or 0),
                        message=data.get("message", "") or "",
                        result_text=data.get("result_text"),
                        result_segments=data.get("result_segments"),
                        error=data.get("error"),
                        diarization_error=data.get("diarization_error"),
                        raw=data,
                    )
                    yield update
                    if update.status in ("completed", "failed"):
                        return
            # socket closed without terminal status -> retry
            retry += 1
            await asyncio.sleep(2)
        except Exception as e:  # noqa: BLE001
            logger.warning("DiarService WS error (%s/%s): %s", retry + 1, max_retries, e)
            retry += 1
            await asyncio.sleep(2)
    yield DiarUpdate(status="failed", error="Потеряна связь с сервисом распознавания")
