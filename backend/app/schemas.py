import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from .models import JobMode, JobStatus


# ===== Auth =====
class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    full_name: str | None = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    # Plain str on purpose: this is an address we already stored, and guest
    # accounts use a synthetic one. Validation belongs on input (UserRegister /
    # UserLogin), not on echoing a value back.
    email: str
    full_name: str | None
    is_admin: bool
    is_guest: bool = False
    created_at: datetime


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


# ===== Jobs =====
class Segment(BaseModel):
    id: int | None = None
    speaker: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    text: str | None = None


class JobListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    batch_id: uuid.UUID | None = None
    original_filename: str
    file_size: int
    mode: JobMode
    status: JobStatus
    progress: int
    stage_message: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class JobDetail(JobListItem):
    result_text: str | None
    result_segments: list[Segment] | None
    report_html: str | None
    diarization_note: str | None = None
    has_report_pdf: bool = False
    has_transcript_pdf: bool = False


class UploadInit(BaseModel):
    filename: str
    file_size: int = Field(ge=0)
    mode: JobMode = JobMode.transcription
    # Same value for every file of one upload -> they form a downloadable group.
    batch_id: uuid.UUID | None = None


class UploadInitOut(BaseModel):
    upload_id: str
    chunk_size: int


class UploadCompleteOut(BaseModel):
    job_id: uuid.UUID


class RerunRequest(BaseModel):
    """Re-process an existing transcript in another mode."""

    mode: JobMode


class ArchiveRequest(BaseModel):
    """Download the results of several jobs as one ZIP."""

    job_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    kind: Literal["all", "transcript", "report"] = "all"
