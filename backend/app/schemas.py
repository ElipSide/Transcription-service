import uuid
from datetime import datetime

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
    email: EmailStr
    full_name: str | None
    is_admin: bool
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
    has_report_pdf: bool = False
    has_transcript_pdf: bool = False


class UploadInit(BaseModel):
    filename: str
    file_size: int = Field(ge=0)
    mode: JobMode = JobMode.transcription


class UploadInitOut(BaseModel):
    upload_id: str
    chunk_size: int


class UploadCompleteOut(BaseModel):
    job_id: uuid.UUID
