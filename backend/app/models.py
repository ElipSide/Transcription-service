import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, enum.Enum):
    uploading = "uploading"      # chunks being received
    queued = "queued"           # accepted, waiting for processing
    waiting = "waiting"         # DiarService unavailable -> auto-retried later
    processing = "processing"   # being recognized/diarized
    completed = "completed"
    failed = "failed"


class JobMode(str, enum.Enum):
    transcription = "transcription"
    report = "report"
    ai_extract = "ai_extract"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(default=False, nullable=False)
    # Anonymous session: works without registering, purged together with its
    # jobs once the session is gone (see services/cleanup.py).
    is_guest: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    jobs: Mapped[list["Job"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    # source file
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    stored_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Files uploaded together share one batch_id, so a group can be downloaded
    # as a whole without picking every job by hand.
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    mode: Mapped[JobMode] = mapped_column(
        Enum(JobMode, native_enum=False), default=JobMode.transcription, nullable=False
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False), default=JobStatus.uploading, nullable=False, index=True
    )
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stage_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # external task reference
    diar_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # non-fatal diarization warning surfaced from DiarService (e.g. MSDD failed
    # -> everything collapsed to one speaker). Transcript is still valid.
    diarization_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # results
    result_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_segments: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    report_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_pdf_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    transcript_pdf_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="jobs")


class ServiceToken(Base):
    """Long-lived, opaque API token for machine integrations (e.g. the call
    archive). Acts as its owning user on the jobs endpoints — an alternative to
    storing that account's password and juggling the 14-day refresh cycle.

    Only the SHA-256 hash is stored; the raw token is shown once at creation.
    The token is high-entropy random, so a fast hash is sufficient (unlike user
    passwords, which stay bcrypt-hashed).
    """

    __tablename__ = "service_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    # Comma-separated scopes, reserved for future narrowing. Today a token acts
    # as its owner across the jobs endpoints.
    scopes: Mapped[str] = mapped_column(String(255), default="jobs", nullable=False)
    revoked: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship()
