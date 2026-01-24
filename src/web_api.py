from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse

from src.viewer_db import (
    default_db_path,
    last_upload_time_for_camera as db_last_upload_time_for_camera,
    list_cameras as db_list_cameras,
    list_clips_for_day as db_list_clips_for_day,
    resolve_clip_by_id as db_resolve_clip_by_id,
)
from src.web_models import CameraOut, ClipDetailOut, ClipOut


def _out_dir() -> Path:
    return Path(str(os.getenv("OUT_DIR", "/data")).strip() or "/data")


def _require_token(request: Request) -> None:
    token = str(os.getenv("VIEWER_AUTH_TOKEN", "")).strip()
    if not token:
        return
    auth = request.headers.get("authorization") or ""
    if auth.strip() != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="unauthorized")


AuthDep = Annotated[None, Depends(_require_token)]

app = FastAPI(title="Camera Storage Viewer")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/cameras", response_model=list[CameraOut])
def list_cameras(_: AuthDep) -> list[CameraOut]:
    out_dir = _out_dir()
    db_path = default_db_path(out_dir)
    cameras: list[CameraOut] = []
    for camera_id in db_list_cameras(out_dir=out_dir, db_path=db_path):
        cameras.append(
            CameraOut(
                camera_id=camera_id,
                display_name=camera_id,
                last_upload_at=db_last_upload_time_for_camera(out_dir=out_dir, db_path=db_path, camera_id=camera_id),
            )
        )
    return cameras


@app.get("/api/cameras/{camera_id}/clips", response_model=list[ClipOut])
def list_clips(
    camera_id: str,
    _: AuthDep,
    day: Annotated[date, Query(alias="date")],
) -> list[ClipOut]:
    out_dir = _out_dir()
    db_path = default_db_path(out_dir)
    clips = db_list_clips_for_day(out_dir=out_dir, db_path=db_path, camera_id=camera_id, day=day)
    return [
        ClipOut(
            clip_id=c.clip_id,
            camera_id=c.camera_id,
            start_time=c.start_time,
            duration_seconds=None,
            size_bytes=c.size_bytes,
            has_thumbnail=(c.find_thumbnail() is not None),
        )
        for c in clips
    ]


@app.get("/api/clips/{clip_id}", response_model=ClipDetailOut)
def get_clip(clip_id: str, _: AuthDep) -> ClipDetailOut:
    out_dir = _out_dir()
    db_path = default_db_path(out_dir)
    clip = db_resolve_clip_by_id(out_dir=out_dir, db_path=db_path, clip_id=clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="clip not found")

    return ClipDetailOut(
        clip_id=clip.clip_id,
        camera_id=clip.camera_id,
        start_time=clip.start_time,
        duration_seconds=None,
        size_bytes=clip.size_bytes,
        has_thumbnail=(clip.find_thumbnail() is not None),
        filename=clip.filename,
    )


@app.get("/api/clips/{clip_id}/thumbnail")
def get_thumbnail(
    clip_id: str,
    _: AuthDep,
    size: Annotated[str, Query(pattern="^(small|large)$")] = "small",
) -> FileResponse:
    out_dir = _out_dir()
    db_path = default_db_path(out_dir)
    clip = db_resolve_clip_by_id(out_dir=out_dir, db_path=db_path, clip_id=clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="clip not found")

    thumb = clip.find_thumbnail()
    if thumb is None:
        raise HTTPException(status_code=404, detail="thumbnail not found")

    # Size variants are a future enhancement; for now serve the best available.
    _ = size
    return FileResponse(path=str(thumb), media_type="image/jpeg")


@app.get("/media/{clip_id}")
def stream_media(clip_id: str, _: AuthDep) -> FileResponse:
    out_dir = _out_dir()
    db_path = default_db_path(out_dir)
    clip = db_resolve_clip_by_id(out_dir=out_dir, db_path=db_path, clip_id=clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="clip not found")

    return FileResponse(path=str(clip.abs_path), media_type="video/mp4")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("WEB_PORT", "8081")))
