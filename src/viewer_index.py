from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path


VIDEO_EXTS = {".mp4", ".mov", ".mkv"}
THUMB_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


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
