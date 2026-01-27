from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.viewer_index import THUMB_EXTS, VIDEO_EXTS


@dataclass(frozen=True)
class IngestResult:
    scanned: int
    moved: int
    skipped_recent: int
    skipped_nonvideo: int
    skipped_existing: int


def _utc_from_mtime(path: Path) -> datetime:
    st = path.stat()
    return datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)


def _library_dest_for(out_dir: Path, *, camera_id: str, src_path: Path) -> Path:
    ts = _utc_from_mtime(src_path)
    return out_dir / "videos" / camera_id / f"{ts.year:04d}" / f"{ts.month:02d}" / f"{ts.day:02d}" / src_path.name


def ingest_incoming_once(
    *,
    out_dir: Path,
    min_age_seconds: int = 60,
    apply: bool = False,
) -> IngestResult:
    incoming_root = out_dir / "incoming"
    if not incoming_root.exists():
        return IngestResult(scanned=0, moved=0, skipped_recent=0, skipped_nonvideo=0, skipped_existing=0)

    now = time.time()

    scanned = 0
    moved = 0
    skipped_recent = 0
    skipped_nonvideo = 0
    skipped_existing = 0

    for camera_dir in sorted(incoming_root.iterdir()):
        if not camera_dir.is_dir():
            continue
        camera_id = camera_dir.name

        for src_path in sorted(camera_dir.rglob("*")):
            if not src_path.is_file():
                continue

            scanned += 1

            # Ignore sidecars here; they get moved alongside the video.
            if src_path.suffix.lower() not in VIDEO_EXTS:
                skipped_nonvideo += 1
                continue

            # Skip partial/temporary uploads.
            lower_name = src_path.name.lower()
            if lower_name.endswith(".tmp") or lower_name.endswith(".part"):
                skipped_recent += 1
                continue

            st = src_path.stat()
            age = now - st.st_mtime
            if age < float(min_age_seconds):
                skipped_recent += 1
                continue

            dest_path = _library_dest_for(out_dir, camera_id=camera_id, src_path=src_path)
            if dest_path.exists():
                skipped_existing += 1
                continue

            # Move the video.
            if apply:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                src_path.replace(dest_path)
            moved += 1

            # Move any sidecar thumbnails with the same stem.
            for ext in THUMB_EXTS:
                sidecar = src_path.with_suffix(ext)
                if sidecar.exists() and sidecar.is_file():
                    sidecar_dest = dest_path.with_suffix(ext)
                    if sidecar_dest.exists():
                        continue
                    if apply:
                        sidecar_dest.parent.mkdir(parents=True, exist_ok=True)
                        sidecar.replace(sidecar_dest)

    return IngestResult(
        scanned=scanned,
        moved=moved,
        skipped_recent=skipped_recent,
        skipped_nonvideo=skipped_nonvideo,
        skipped_existing=skipped_existing,
    )
