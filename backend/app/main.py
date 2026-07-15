import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import init_db
from .routers import auth, jobs
from .services import diar_client
from .services.processor import retry_waiting_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Prime the availability flag and start the DiarService reconciler, which
    # flushes the "waiting" queue once the service becomes reachable again.
    await diar_client.refresh_health()
    retry_task = asyncio.create_task(retry_waiting_loop())
    try:
        yield
    finally:
        retry_task.cancel()


app = FastAPI(title="Transcription Service", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(jobs.router)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/diar/status")
async def diar_status() -> dict:
    """Cached DiarService availability for the browser status banner."""
    return {"available": diar_client.is_available()}
