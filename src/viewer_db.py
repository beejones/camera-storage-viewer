from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from src.viewer_index import VIDEO_EXTS, _clip_id_for, _utc_from_timestamp


@dataclass(frozen=True)
class DbClip:
    clip_id: str
    camera_id: str
    abs_path: Path
    start_time: datetime
    size_bytes: int
    mtime_ns: int

    @property
    def filename(self) -> str:
        return self.abs_path.name

    def find_thumbnail(self) -> Path | None:
        for ext in (".jpg", ".jpeg", ".webp", ".png"):
            candidate = self.abs_path.with_suffix(ext)
            if candidate.exists() and candidate.is_file():
                return candidate
        return None


def default_db_path(out_dir: Path) -> Path:
    return out_dir / "databases" / "index.sqlite"


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(db_path: Path) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clips (
              clip_id TEXT PRIMARY KEY,
              camera_id TEXT NOT NULL,
              rel_path TEXT NOT NULL,
              start_time_utc TEXT NOT NULL,
              size_bytes INTEGER NOT NULL,
              mtime_ns INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_clips_camera_time ON clips(camera_id, start_time_utc)")


def _iter_video_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() in VIDEO_EXTS:
            files.append(path)
    return files


def update_index_from_incoming(*, out_dir: Path, db_path: Path) -> None:
    init_db(db_path)

    incoming_root = out_dir / "incoming"
    if not incoming_root.exists():
        return

    upserts: list[tuple[str, str, str, str, int, int]] = []

    for camera_dir in incoming_root.iterdir():
        if not camera_dir.is_dir():
            continue
        camera_id = camera_dir.name
        for abs_path in _iter_video_files(camera_dir):
            st = abs_path.stat()
            rel_path = str(abs_path.relative_to(out_dir))
            clip_id = _clip_id_for(rel_path, st.st_size, st.st_mtime_ns)
            start_time = _utc_from_timestamp(st.st_mtime).isoformat()
            upserts.append((clip_id, camera_id, rel_path, start_time, int(st.st_size), int(st.st_mtime_ns)))

    with _connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO clips(clip_id, camera_id, rel_path, start_time_utc, size_bytes, mtime_ns)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(clip_id) DO UPDATE SET
              camera_id=excluded.camera_id,
              rel_path=excluded.rel_path,
              start_time_utc=excluded.start_time_utc,
              size_bytes=excluded.size_bytes,
              mtime_ns=excluded.mtime_ns
            """,
            upserts,
        )


def list_cameras(*, out_dir: Path, db_path: Path) -> list[str]:
    init_db(db_path)
    update_index_from_incoming(out_dir=out_dir, db_path=db_path)

    with _connect(db_path) as conn:
        rows = conn.execute("SELECT DISTINCT camera_id FROM clips ORDER BY camera_id").fetchall()
        return [str(r["camera_id"]) for r in rows]


def last_upload_time_for_camera(*, out_dir: Path, db_path: Path, camera_id: str) -> datetime | None:
    init_db(db_path)
    update_index_from_incoming(out_dir=out_dir, db_path=db_path)

    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT start_time_utc FROM clips WHERE camera_id=? ORDER BY start_time_utc DESC LIMIT 1",
            (camera_id,),
        ).fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(str(row["start_time_utc"]))


def list_clips_for_day(*, out_dir: Path, db_path: Path, camera_id: str, day: date) -> list[DbClip]:
    init_db(db_path)
    update_index_from_incoming(out_dir=out_dir, db_path=db_path)

    day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    day_end = day_start.replace(hour=23, minute=59, second=59, microsecond=999999)

    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT clip_id, camera_id, rel_path, start_time_utc, size_bytes, mtime_ns
            FROM clips
            WHERE camera_id=? AND start_time_utc BETWEEN ? AND ?
            ORDER BY start_time_utc
            """,
            (camera_id, day_start.isoformat(), day_end.isoformat()),
        ).fetchall()

    clips: list[DbClip] = []
    for r in rows:
        abs_path = out_dir / str(r["rel_path"])
        clips.append(
            DbClip(
                clip_id=str(r["clip_id"]),
                camera_id=str(r["camera_id"]),
                abs_path=abs_path,
                start_time=datetime.fromisoformat(str(r["start_time_utc"])),
                size_bytes=int(r["size_bytes"]),
                mtime_ns=int(r["mtime_ns"]),
            )
        )

    return clips


def resolve_clip_by_id(*, out_dir: Path, db_path: Path, clip_id: str) -> DbClip | None:
    init_db(db_path)
    update_index_from_incoming(out_dir=out_dir, db_path=db_path)

    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT clip_id, camera_id, rel_path, start_time_utc, size_bytes, mtime_ns FROM clips WHERE clip_id=?",
            (clip_id,),
        ).fetchone()

    if row is None:
        return None

    abs_path = out_dir / str(row["rel_path"])
    return DbClip(
        clip_id=str(row["clip_id"]),
        camera_id=str(row["camera_id"]),
        abs_path=abs_path,
        start_time=datetime.fromisoformat(str(row["start_time_utc"])),
        size_bytes=int(row["size_bytes"]),
        mtime_ns=int(row["mtime_ns"]),
    )
