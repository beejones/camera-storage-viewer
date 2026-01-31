from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import subprocess
import time
from shutil import which
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

try:
    import fcntl  # type: ignore
except Exception:  # pragma: no cover
    fcntl = None  # type: ignore

from src.viewer_index import VIDEO_EXTS, _clip_id_for, _utc_from_timestamp


_LOG = logging.getLogger("uvicorn.error")


@dataclass(frozen=True)
class DbClip:
    clip_id: str
    camera_id: str
    abs_path: Path
    start_time: datetime
    duration_seconds: float | None
    size_bytes: int
    mtime_ns: int

    @property
    def filename(self) -> str:
        return self.abs_path.name

    def find_thumbnail(self) -> Path | None:
        for ext in (".jpg", ".jpeg", ".webp", ".png"):
            candidate = self.abs_path.with_suffix(ext)
            if candidate.exists() and candidate.is_file():
                try:
                    if candidate.stat().st_size > 0:
                        return candidate
                except FileNotFoundError:
                    continue
        return None


def default_db_path(out_dir: Path) -> Path:
    return out_dir / "databases" / "index.sqlite"


def _require_int_env(name: str, raw: str | None, *, default: int, min_value: int, max_value: int) -> int:
    s = str(raw or "").strip()
    if not s:
        return default
    try:
        value = int(s)
    except ValueError as e:
        raise ValueError(f"{name} must be an integer") from e
    if value < min_value or value > max_value:
        raise ValueError(f"{name} must be between {min_value} and {max_value}")
    return value


def _sqlite_journal_mode() -> str:
    # Default to DELETE for Azure Files safety (WAL is risky on SMB/NFS).
    mode = str(os.getenv("SQLITE_JOURNAL_MODE", "DELETE")).strip().upper() or "DELETE"
    allowed = {"DELETE", "TRUNCATE", "PERSIST", "MEMORY", "OFF", "WAL"}
    if mode not in allowed:
        raise ValueError(f"SQLITE_JOURNAL_MODE must be one of {sorted(allowed)}")
    return mode


def _sqlite_synchronous() -> str:
    # Default to FULL for durability on network filesystems.
    level = str(os.getenv("SQLITE_SYNCHRONOUS", "FULL")).strip().upper() or "FULL"
    allowed = {"OFF", "NORMAL", "FULL", "EXTRA"}
    if level not in allowed:
        raise ValueError(f"SQLITE_SYNCHRONOUS must be one of {sorted(allowed)}")
    return level


def _sqlite_busy_timeout_ms() -> int:
    return _require_int_env(
        "SQLITE_BUSY_TIMEOUT_MS",
        os.getenv("SQLITE_BUSY_TIMEOUT_MS"),
        default=5000,
        min_value=0,
        max_value=600_000,
    )


def _index_refresh_ttl_seconds() -> int:
    # If 0, refresh every time (legacy behavior). Default to a small TTL.
    return _require_int_env(
        "INDEX_REFRESH_TTL_SECONDS",
        os.getenv("INDEX_REFRESH_TTL_SECONDS"),
        default=30,
        min_value=0,
        max_value=24 * 60 * 60,
    )


def _resolve_index_lock_path(*, out_dir: Path, db_path: Path) -> Path:
    raw = str(os.getenv("INDEX_LOCK_PATH", "")).strip()
    if not raw:
        return db_path.parent / "index.lock"
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p
    return out_dir / p


def _last_refresh_path(db_path: Path) -> Path:
    return db_path.parent / "index.last_refresh"


@contextlib.contextmanager
def _exclusive_lock(lock_path: Path, *, timeout_seconds: float = 30.0):
    """Best-effort process/thread lock via file locking.

    This serializes indexing/writes to SQLite (especially important on Azure Files).
    """

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if fcntl is None:  # pragma: no cover
        # No fcntl available (e.g., non-POSIX). Proceed without a lock.
        yield
        return

    with open(lock_path, "a+") as f:
        start = time.monotonic()
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() - start >= timeout_seconds:
                    raise TimeoutError(f"Timed out waiting for index lock: {lock_path}")
                time.sleep(0.1)

        try:
            yield
        finally:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    busy_timeout_ms = _sqlite_busy_timeout_ms()
    connect_timeout_s = max(0.0, busy_timeout_ms / 1000.0)

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            conn = sqlite3.connect(str(db_path), timeout=connect_timeout_s)
            conn.row_factory = sqlite3.Row

            # IMPORTANT: avoid WAL by default (Azure Files/SMB). These are env-tunable.
            conn.execute(f"PRAGMA journal_mode={_sqlite_journal_mode()}")
            conn.execute(f"PRAGMA synchronous={_sqlite_synchronous()}")
            conn.execute("PRAGMA foreign_keys=ON")
            # NOTE: SQLite does not support parameter binding in PRAGMA statements.
            conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            return conn
        except sqlite3.OperationalError as e:
            last_err = e
            msg = str(e).lower()
            # Common transient on network mounts / during startup.
            if "unable to open database file" in msg or "disk i/o error" in msg:
                time.sleep(0.2 * (attempt + 1))
                continue
            raise

    assert last_err is not None
    raise last_err


def _is_corruption_error(err: BaseException) -> bool:
    msg = str(err).lower()
    return (
        "database disk image is malformed" in msg
        or "file is not a database" in msg
        or "malformed" in msg
        or "database image is malformed" in msg
    )


def _backup_corrupt_db(db_path: Path) -> None:
    if not db_path.exists():
        return
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = db_path.with_name(f"{db_path.name}.corrupt-{ts}")
    try:
        db_path.rename(backup)
    except Exception:
        # Best-effort: if rename fails (e.g. share glitch), leave it in place.
        return

    # If a previous WAL mode left sidecar files, stash them too.
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists():
            try:
                sidecar.rename(backup.with_name(backup.name + suffix))
            except Exception:
                pass


def refresh_index_if_stale(*, out_dir: Path, db_path: Path, ttl_seconds: int | None = None) -> None:
    """Refresh index at most once per TTL.

    Uses a file lock to prevent concurrent writers (threads/processes) from
    corrupting the DB or fighting over locks on network filesystems.
    """

    ttl = _index_refresh_ttl_seconds() if ttl_seconds is None else max(0, int(ttl_seconds))
    lock_path = _resolve_index_lock_path(out_dir=out_dir, db_path=db_path)
    last_refresh = _last_refresh_path(db_path)

    with _exclusive_lock(lock_path, timeout_seconds=30.0):
        if ttl > 0 and last_refresh.exists():
            try:
                age_s = time.time() - last_refresh.stat().st_mtime
                if age_s >= 0 and age_s < ttl:
                    return
            except FileNotFoundError:
                pass

        try:
            _update_index_from_storage_unlocked(out_dir=out_dir, db_path=db_path)
        except sqlite3.Error as e:
            if _is_corruption_error(e):
                _LOG.error("SQLite corruption detected; rebuilding index DB: %s (%s)", str(db_path), str(e))
                _backup_corrupt_db(db_path)
                _update_index_from_storage_unlocked(out_dir=out_dir, db_path=db_path)
            else:
                raise

        last_refresh.parent.mkdir(parents=True, exist_ok=True)
        try:
            last_refresh.write_text(datetime.now(timezone.utc).isoformat() + "\n")
        except Exception:
            # If we can't persist last-refresh, we still prefer serving requests.
            pass


def init_db(db_path: Path) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clips (
              clip_id TEXT PRIMARY KEY,
              camera_id TEXT NOT NULL,
              rel_path TEXT NOT NULL,
              start_time_utc TEXT NOT NULL,
              duration_seconds REAL,
              size_bytes INTEGER NOT NULL,
              mtime_ns INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_clips_camera_time ON clips(camera_id, start_time_utc)")

        # Handle older DBs created before duration_seconds existed.
        cols = {str(r["name"]) for r in conn.execute("PRAGMA table_info(clips)").fetchall()}
        if "duration_seconds" not in cols:
            conn.execute("ALTER TABLE clips ADD COLUMN duration_seconds REAL")


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


def _ffprobe_available() -> bool:
    return which("ffprobe") is not None


def _probe_duration_seconds(path: Path) -> float | None:
    if not _ffprobe_available():
        return None

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=nw=1:nk=1",
        str(path),
    ]

    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True).strip()
        if not out:
            return None
        return float(out)
    except Exception:
        return None


def _iter_media_roots(out_dir: Path) -> list[tuple[str, Path]]:
    # Scan both pre-ingest (incoming) and post-ingest (videos) locations.
    return [
        ("incoming", out_dir / "incoming"),
        ("videos", out_dir / "videos"),
    ]


def _update_index_from_storage_unlocked(*, out_dir: Path, db_path: Path) -> None:
    init_db(db_path)

    upserts: list[tuple[str, str, str, str, float | None, int, int]] = []

    for _root_name, root in _iter_media_roots(out_dir):
        if not root.exists():
            continue
        for camera_dir in root.iterdir():
            if not camera_dir.is_dir():
                continue
            camera_id = camera_dir.name
            for abs_path in _iter_video_files(camera_dir):
                try:
                    st = abs_path.stat()
                except FileNotFoundError:
                    # Files can disappear between directory enumeration and stat (uploads/renames).
                    continue
                if st.st_size <= 0:
                    continue
                rel_path = str(abs_path.relative_to(out_dir))
                clip_id = _clip_id_for(rel_path, st.st_size, st.st_mtime_ns)
                start_time = _utc_from_timestamp(st.st_mtime).isoformat()
                # Duration probing via ffprobe can be slow, especially on network filesystems.
                # We keep duration optional (nullable) and do not probe during indexing.
                upserts.append((clip_id, camera_id, rel_path, start_time, None, int(st.st_size), int(st.st_mtime_ns)))

    with _connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO clips(clip_id, camera_id, rel_path, start_time_utc, duration_seconds, size_bytes, mtime_ns)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(clip_id) DO UPDATE SET
              camera_id=excluded.camera_id,
              rel_path=excluded.rel_path,
              start_time_utc=excluded.start_time_utc,
              duration_seconds=COALESCE(excluded.duration_seconds, clips.duration_seconds),
              size_bytes=excluded.size_bytes,
              mtime_ns=excluded.mtime_ns
            """,
            upserts,
        )


def update_index_from_storage(*, out_dir: Path, db_path: Path) -> None:
    """Force an immediate index refresh.

    This is still serialized via the same index lock to avoid concurrent writers.
    """

    lock_path = _resolve_index_lock_path(out_dir=out_dir, db_path=db_path)
    last_refresh = _last_refresh_path(db_path)
    with _exclusive_lock(lock_path, timeout_seconds=30.0):
        try:
            _update_index_from_storage_unlocked(out_dir=out_dir, db_path=db_path)
        except sqlite3.Error as e:
            if _is_corruption_error(e):
                _LOG.error("SQLite corruption detected; rebuilding index DB: %s (%s)", str(db_path), str(e))
                _backup_corrupt_db(db_path)
                _update_index_from_storage_unlocked(out_dir=out_dir, db_path=db_path)
            else:
                raise

        last_refresh.parent.mkdir(parents=True, exist_ok=True)
        try:
            last_refresh.write_text(datetime.now(timezone.utc).isoformat() + "\n")
        except Exception:
            pass


def list_cameras(*, out_dir: Path, db_path: Path, refresh_index: bool = True) -> list[str]:
    init_db(db_path)
    if refresh_index:
        refresh_index_if_stale(out_dir=out_dir, db_path=db_path)

    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT camera_id FROM clips WHERE size_bytes > 0 ORDER BY camera_id"
        ).fetchall()
        return [str(r["camera_id"]) for r in rows]


def last_upload_time_for_camera(
    *,
    out_dir: Path,
    db_path: Path,
    camera_id: str,
    refresh_index: bool = True,
) -> datetime | None:
    init_db(db_path)
    if refresh_index:
        refresh_index_if_stale(out_dir=out_dir, db_path=db_path)

    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT start_time_utc FROM clips WHERE camera_id=? AND size_bytes > 0 ORDER BY start_time_utc DESC LIMIT 1",
            (camera_id,),
        ).fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(str(row["start_time_utc"]))


def list_clips_for_day(
    *,
    out_dir: Path,
    db_path: Path,
    camera_id: str,
    day: date,
    refresh_index: bool = True,
) -> list[DbClip]:
    init_db(db_path)
    if refresh_index:
        refresh_index_if_stale(out_dir=out_dir, db_path=db_path)

    day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    day_end = day_start.replace(hour=23, minute=59, second=59, microsecond=999999)

    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT clip_id, camera_id, rel_path, start_time_utc, duration_seconds, size_bytes, mtime_ns
            FROM clips
            WHERE camera_id=? AND size_bytes > 0 AND start_time_utc BETWEEN ? AND ?
            ORDER BY start_time_utc
            """,
            (camera_id, day_start.isoformat(), day_end.isoformat()),
        ).fetchall()

    clips: list[DbClip] = []
    for r in rows:
        abs_path = out_dir / str(r["rel_path"])
        try:
            st = abs_path.stat()
        except FileNotFoundError:
            continue
        if st.st_size <= 0:
            continue
        clips.append(
            DbClip(
                clip_id=str(r["clip_id"]),
                camera_id=str(r["camera_id"]),
                abs_path=abs_path,
                start_time=datetime.fromisoformat(str(r["start_time_utc"])),
                duration_seconds=(None if r["duration_seconds"] is None else float(r["duration_seconds"])),
                size_bytes=int(r["size_bytes"]),
                mtime_ns=int(r["mtime_ns"]),
            )
        )

    return clips


def resolve_clip_by_id(
    *,
    out_dir: Path,
    db_path: Path,
    clip_id: str,
    refresh_index: bool = True,
) -> DbClip | None:
    init_db(db_path)
    if refresh_index:
        refresh_index_if_stale(out_dir=out_dir, db_path=db_path)

    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT clip_id, camera_id, rel_path, start_time_utc, duration_seconds, size_bytes, mtime_ns FROM clips WHERE clip_id=? AND size_bytes > 0",
            (clip_id,),
        ).fetchone()

    if row is None:
        return None

    abs_path = out_dir / str(row["rel_path"])
    try:
        st = abs_path.stat()
    except FileNotFoundError:
        # Best-effort cleanup of stale DB entries.
        with _connect(db_path) as conn:
            conn.execute("DELETE FROM clips WHERE clip_id=?", (clip_id,))
        return None

    if st.st_size <= 0:
        # Treat zero-byte files as missing/incomplete uploads.
        with _connect(db_path) as conn:
            conn.execute("DELETE FROM clips WHERE clip_id=?", (clip_id,))
        return None
    return DbClip(
        clip_id=str(row["clip_id"]),
        camera_id=str(row["camera_id"]),
        abs_path=abs_path,
        start_time=datetime.fromisoformat(str(row["start_time_utc"])),
        duration_seconds=(None if row["duration_seconds"] is None else float(row["duration_seconds"])),
        size_bytes=int(row["size_bytes"]),
        mtime_ns=int(row["mtime_ns"]),
    )
