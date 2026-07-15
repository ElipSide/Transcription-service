from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql+asyncpg://transcribe:transcribe@localhost:5432/transcribe"

    # Auth
    secret_key: str = "change-me"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 14
    algorithm: str = "HS256"

    # DiarService (port 8443 is HTTPS / nginx with a self-signed cert)
    diar_base_url: str = "https://109.202.9.107:8443/diaraudio"
    diar_ws_url: str = "wss://109.202.9.107:8443/diaraudio"
    diar_upload_timeout: int = 360
    diar_verify_ssl: bool = False  # self-signed cert -> skip verification
    # How often the reconciler pings DiarService and flushes the waiting queue.
    diar_health_interval: int = 15
    diar_health_timeout: float = 6.0

    # DeepSeek
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    # Storage
    storage_dir: str = "storage"
    max_file_size: int = 2 * 1024 * 1024 * 1024
    chunk_dir: str = "storage/chunks"

    # ffmpeg
    ffmpeg_bin: str = "ffmpeg"

    # CORS
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def storage_path(self) -> Path:
        p = Path(self.storage_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def chunk_path(self) -> Path:
        p = Path(self.chunk_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
