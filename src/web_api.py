from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path
from typing import Annotated

from dotenv import dotenv_values
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.thumbnails import ensure_thumbnail, generated_thumbnail_path, load_thumbnail_config
from src.viewer_db import (
    default_db_path,
    last_upload_time_for_camera as db_last_upload_time_for_camera,
    list_cameras as db_list_cameras,
    list_clips_for_day as db_list_clips_for_day,
    resolve_clip_by_id as db_resolve_clip_by_id,
)
from src.web_models import CameraOut, ClipDetailOut, ClipOut


# Use Uvicorn's logger so messages reliably show up in container logs.
_LOG = logging.getLogger("uvicorn.error")


def _merged_env() -> dict[str, str]:
    """Return merged env from (optional) dotenv file + process env.

    Azure deployments can fetch a runtime .env into RUNTIME_ENV_PATH via azure_start.sh.
    Locally, docker-compose typically supplies env vars directly.
    """

    env_path_candidates = [
        Path(str(os.getenv("RUNTIME_ENV_PATH", "")).strip()).expanduser() if os.getenv("RUNTIME_ENV_PATH") else None,
        Path("/app/.env"),
    ]

    file_kv: dict[str, str] = {}
    for candidate in env_path_candidates:
        if not candidate:
            continue
        if candidate.exists():
            raw = dotenv_values(candidate)
            for k, v in raw.items():
                if not k:
                    continue
                file_kv[str(k)] = "" if v is None else str(v)
            break

    merged = dict(file_kv)
    merged.update(os.environ)
    return merged


def _out_dir() -> Path:
    env = _merged_env()
    return Path(str(env.get("OUT_DIR", "/data")).strip() or "/data")


def _list_camera_dirs(out_dir: Path) -> list[str]:
    incoming_dir = out_dir / "incoming"
    if not incoming_dir.exists() or not incoming_dir.is_dir():
        return []
    return sorted([p.name for p in incoming_dir.iterdir() if p.is_dir()])


def _safe_ftp_env_for_logs(env: dict[str, str]) -> dict[str, str]:
    def _get(key: str) -> str:
        v = env.get(key)
        return "" if v is None else str(v)

    def _presence(key: str) -> str:
        v = _get(key).strip()
        return "set" if v else "unset"

    users_json = _get("FTP_USERS_JSON")

    # IMPORTANT: FTP_USERS_JSON contains passwords, so never log its value.
    return {
        "FTP_BIND_HOST": _get("FTP_BIND_HOST"),
        "FTP_PORT": _get("FTP_PORT"),
        "FTP_PUBLIC_HOST": _get("FTP_PUBLIC_HOST"),
        "FTP_PASSIVE_PORT_MIN": _get("FTP_PASSIVE_PORT_MIN"),
        "FTP_PASSIVE_PORT_MAX": _get("FTP_PASSIVE_PORT_MAX"),
        "FTP_PERMIT_FOREIGN_ADDRESSES": _get("FTP_PERMIT_FOREIGN_ADDRESSES"),
        "FTP_INCOMING_DIR": _get("FTP_INCOMING_DIR"),
        "FTP_SPOOL_DIR": _get("FTP_SPOOL_DIR"),
        "FTP_CAMERA_ID": _get("FTP_CAMERA_ID"),
        "FTP_USERNAME": _get("FTP_USERNAME"),
        "FTP_PASSWORD": _presence("FTP_PASSWORD"),
        "FTP_USERS_JSON": f"{_presence('FTP_USERS_JSON')} (len={len(users_json)})" if users_json else "unset",
    }

app = FastAPI(title="Camera Storage Viewer")


@app.on_event("startup")
def _log_startup_state() -> None:
    env = _merged_env()
    out_dir = _out_dir()

    incoming_dir = out_dir / "incoming"
    try:
        camera_dirs = _list_camera_dirs(out_dir)
    except Exception:
        camera_dirs = []

    runtime_env_path = str(os.getenv("RUNTIME_ENV_PATH", "/app/.env")).strip() or "/app/.env"
    runtime_env_exists = Path(runtime_env_path).exists()
    keyvault_uri_set = bool(str(env.get("AZURE_KEYVAULT_URI", "")).strip())

    # Never log secrets (APP_SECRET / BASIC_AUTH_HASH). FTP_PASSWORD is logged as presence only.
    safe_env = {
        "AZURE_KEYVAULT_URI": "set" if keyvault_uri_set else "unset",
        "RUNTIME_ENV_PATH": runtime_env_path,
        "RUNTIME_ENV_EXISTS": "yes" if runtime_env_exists else "no",
        "OUT_DIR": str(out_dir),
        "WEB_PORT": "set" if str(env.get("WEB_PORT", "")).strip() else "unset",
        "THUMBNAIL_GENERATION": "set" if str(env.get("THUMBNAIL_GENERATION", "")).strip() else "unset",
        **_safe_ftp_env_for_logs(env),
    }

    _LOG.info(
        "startup: out_dir=%s exists=%s incoming=%s camera_dirs=%s env=%s",
        str(out_dir),
        "yes" if out_dir.exists() else "no",
        str(incoming_dir),
        f"{len(camera_dirs)} ({', '.join(camera_dirs[:5])}{'...' if len(camera_dirs) > 5 else ''})",
        safe_env,
    )

_BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(_BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(_BASE_DIR / "templates"))


@app.get("/")
def viewer_home(request: Request) -> object:
    return templates.TemplateResponse(request, "index.html")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/cameras", response_model=list[CameraOut])
def list_cameras() -> list[CameraOut]:
    env = _merged_env()
    out_dir = _out_dir()
    db_path = default_db_path(out_dir)

    # Log every refresh so it's visible in container logs.
    try:
        camera_dirs = _list_camera_dirs(out_dir)
    except Exception:
        camera_dirs = []
    _LOG.info(
        "GET /api/cameras: out_dir=%s out_dir_exists=%s db=%s db_exists=%s incoming=%s incoming_exists=%s incoming_camera_dirs=%s ftp=%s",
        str(out_dir),
        "yes" if out_dir.exists() else "no",
        str(db_path),
        "yes" if db_path.exists() else "no",
        str(out_dir / "incoming"),
        "yes" if (out_dir / "incoming").exists() else "no",
        f"{len(camera_dirs)} ({', '.join(camera_dirs[:10])}{'...' if len(camera_dirs) > 10 else ''})",
        _safe_ftp_env_for_logs(env),
    )

    cameras: list[CameraOut] = []
    for camera_id in db_list_cameras(out_dir=out_dir, db_path=db_path):
        cameras.append(
            CameraOut(
                camera_id=camera_id,
                display_name=camera_id,
                last_upload_at=db_last_upload_time_for_camera(out_dir=out_dir, db_path=db_path, camera_id=camera_id),
            )
        )

    _LOG.info(
        "GET /api/cameras: returning %s cameras (%s)",
        len(cameras),
        ", ".join([c.camera_id for c in cameras[:10]]) + ("..." if len(cameras) > 10 else ""),
    )
    return cameras


@app.get("/api/cameras/{camera_id}/clips", response_model=list[ClipOut])
def list_clips(
    camera_id: str,
    day: Annotated[date, Query(alias="date")],
) -> list[ClipOut]:
    out_dir = _out_dir()
    db_path = default_db_path(out_dir)
    clips = db_list_clips_for_day(out_dir=out_dir, db_path=db_path, camera_id=camera_id, day=day)

    thumb_cfg = load_thumbnail_config()
    return [
        ClipOut(
            clip_id=c.clip_id,
            camera_id=c.camera_id,
            start_time=c.start_time,
            duration_seconds=c.duration_seconds,
            size_bytes=c.size_bytes,
            has_thumbnail=(
                (c.find_thumbnail() is not None)
                or generated_thumbnail_path(out_dir, c, size="small", cfg=thumb_cfg).exists()
                or generated_thumbnail_path(out_dir, c, size="large", cfg=thumb_cfg).exists()
            ),
        )
        for c in clips
    ]


@app.get("/api/clips/{clip_id}", response_model=ClipDetailOut)
def get_clip(clip_id: str) -> ClipDetailOut:
    out_dir = _out_dir()
    db_path = default_db_path(out_dir)
    clip = db_resolve_clip_by_id(out_dir=out_dir, db_path=db_path, clip_id=clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="clip not found")

    return ClipDetailOut(
        clip_id=clip.clip_id,
        camera_id=clip.camera_id,
        start_time=clip.start_time,
        duration_seconds=clip.duration_seconds,
        size_bytes=clip.size_bytes,
        has_thumbnail=(clip.find_thumbnail() is not None),
        filename=clip.filename,
    )


@app.get("/api/clips/{clip_id}/thumbnail")
def get_thumbnail(
    clip_id: str,
    size: Annotated[str, Query(pattern="^(small|large)$")] = "small",
) -> FileResponse:
    out_dir = _out_dir()
    db_path = default_db_path(out_dir)
    clip = db_resolve_clip_by_id(out_dir=out_dir, db_path=db_path, clip_id=clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="clip not found")

    thumb = ensure_thumbnail(out_dir, clip, size=size)
    if thumb is None:
        raise HTTPException(status_code=404, detail="thumbnail not found")

    media_type = "image/jpeg"
    if thumb.suffix.lower() == ".webp":
        media_type = "image/webp"
    elif thumb.suffix.lower() == ".png":
        media_type = "image/png"

    return FileResponse(path=str(thumb), media_type=media_type)


@app.get("/media/{clip_id}")
def stream_media(clip_id: str) -> FileResponse:
    out_dir = _out_dir()
    db_path = default_db_path(out_dir)
    clip = db_resolve_clip_by_id(out_dir=out_dir, db_path=db_path, clip_id=clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="clip not found")

    return FileResponse(path=str(clip.abs_path), media_type="video/mp4")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("WEB_PORT", "8081")))
