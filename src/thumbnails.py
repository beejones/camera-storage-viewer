from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.viewer_db import DbClip


@dataclass(frozen=True)
class ThumbnailConfig:
    enabled: bool
    # Directory under OUT_DIR used for generated thumbnails.
    root_dirname: str = "thumbnails"


def load_thumbnail_config() -> ThumbnailConfig:
    raw = str(os.getenv("THUMBNAIL_GENERATION", "")).strip().lower()
    enabled = raw in {"1", "true", "yes", "y", "on"}
    return ThumbnailConfig(enabled=enabled)


def _date_path_parts(dt: datetime) -> tuple[str, str, str]:
    return (f"{dt.year:04d}", f"{dt.month:02d}", f"{dt.day:02d}")


def _thumb_dir_for(out_dir: Path, clip: DbClip, cfg: ThumbnailConfig) -> Path:
    y, m, d = _date_path_parts(clip.start_time)
    return out_dir / cfg.root_dirname / clip.camera_id / y / m / d


def generated_thumbnail_path(out_dir: Path, clip: DbClip, *, size: str, cfg: ThumbnailConfig) -> Path:
    if size not in {"small", "large"}:
        raise ValueError("size must be small or large")
    suffix = "_small" if size == "small" else "_large"
    return _thumb_dir_for(out_dir, clip, cfg) / f"{clip.clip_id}{suffix}.jpg"


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _run_ffmpeg_extract(*, input_path: Path, output_path: Path, width: int) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Extract a representative frame near the start; cameras often record fixed-length clips.
    # Use scale to generate predictable thumbnail sizes.
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        "1",
        "-i",
        str(input_path),
        "-frames:v",
        "1",
        "-vf",
        f"scale={width}:-1",
        "-q:v",
        "4",
        str(output_path),
    ]

    subprocess.run(cmd, check=True)


def ensure_thumbnail(out_dir: Path, clip: DbClip, *, size: str) -> Path | None:
    # Prefer sidecar thumbnails if present.
    sidecar = clip.find_thumbnail()
    if sidecar is not None:
        return sidecar

    cfg = load_thumbnail_config()
    if not cfg.enabled:
        return None

    if not _ffmpeg_available():
        return None

    target = generated_thumbnail_path(out_dir, clip, size=size, cfg=cfg)
    if target.exists() and target.is_file():
        return target

    width = 320 if size == "small" else 640

    try:
        _run_ffmpeg_extract(input_path=clip.abs_path, output_path=target, width=width)
    except subprocess.CalledProcessError:
        # Treat as missing thumbnail for now; caller decides 404.
        return None

    if target.exists() and target.is_file():
        return target

    return None
