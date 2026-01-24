from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path


VIDEO_EXTS = {".mp4", ".mov", ".mkv"}
THUMB_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass(frozen=True)
class ClipRecord:
    clip_id: str
    camera_id: str
    path: Path
    start_time: datetime
    size_bytes: int

    @property
    def filename(self) -> str:
        return self.path.name

    def find_thumbnail(self) -> Path | None:
        # Sidecar next to video: same stem.
        for ext in (".jpg", ".jpeg", ".webp", ".png"):
            candidate = self.path.with_suffix(ext)
            if candidate.exists() and candidate.is_file():
                return candidate
        return None


def _utc_from_timestamp(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _clip_id_for(rel_path: str, size_bytes: int, mtime_ns: int) -> str:
    h = hashlib.sha256()
    h.update(rel_path.encode("utf-8"))
    h.update(b"\0")
    h.update(str(size_bytes).encode("ascii"))
    h.update(b"\0")
    h.update(str(mtime_ns).encode("ascii"))
    return h.hexdigest()[:16]


def iter_camera_ids(incoming_root: Path) -> list[str]:
    if not incoming_root.exists():
        return []
    camera_ids: list[str] = []
    for entry in sorted(incoming_root.iterdir()):
        if entry.is_dir():
            camera_ids.append(entry.name)
    return camera_ids


def _iter_video_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() in VIDEO_EXTS:
            files.append(path)
    return sorted(files)


def list_clips_for_day(*, out_dir: Path, camera_id: str, day: date) -> list[ClipRecord]:
    # MVP behavior: scan incoming for the camera and filter by file mtime date (UTC).
    incoming_root = out_dir / "incoming" / camera_id
    clips: list[ClipRecord] = []

    for video_path in _iter_video_files(incoming_root):
        st = video_path.stat()
        start_time = _utc_from_timestamp(st.st_mtime)
        if start_time.date() != day:
            continue

        rel_path = str(video_path.relative_to(out_dir))
        clip_id = _clip_id_for(rel_path, st.st_size, st.st_mtime_ns)
        clips.append(
            ClipRecord(
                clip_id=clip_id,
                camera_id=camera_id,
                path=video_path,
                start_time=start_time,
                size_bytes=int(st.st_size),
            )
        )

    clips.sort(key=lambda c: c.start_time)
    return clips


def resolve_clip_by_id(*, out_dir: Path, clip_id: str) -> ClipRecord | None:
    # MVP: brute-force scan. Replace with SQLite index later.
    incoming_root = out_dir / "incoming"
    if not incoming_root.exists():
        return None

    for camera_id in iter_camera_ids(incoming_root):
        for video_path in _iter_video_files(incoming_root / camera_id):
            st = video_path.stat()
            rel_path = str(video_path.relative_to(out_dir))
            candidate_id = _clip_id_for(rel_path, st.st_size, st.st_mtime_ns)
            if candidate_id == clip_id:
                return ClipRecord(
                    clip_id=candidate_id,
                    camera_id=camera_id,
                    path=video_path,
                    start_time=_utc_from_timestamp(st.st_mtime),
                    size_bytes=int(st.st_size),
                )

    return None


def last_upload_time_for_camera(*, out_dir: Path, camera_id: str) -> datetime | None:
    incoming_root = out_dir / "incoming" / camera_id
    latest: datetime | None = None
    for video_path in _iter_video_files(incoming_root):
        st = video_path.stat()
        t = _utc_from_timestamp(st.st_mtime)
        if latest is None or t > latest:
            latest = t
    return latest
