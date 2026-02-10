from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CameraOut(BaseModel):
    camera_id: str
    display_name: str | None = None
    last_upload_at: datetime | None = None


class ClipOut(BaseModel):
    clip_id: str
    camera_id: str
    start_time: datetime
    duration_seconds: float | None = None
    size_bytes: int = Field(ge=0)
    has_thumbnail: bool = False


class ClipDetailOut(ClipOut):
    filename: str
