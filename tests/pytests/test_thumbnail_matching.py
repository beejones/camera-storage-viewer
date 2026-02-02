from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.viewer_db import DbClip


def test_find_thumbnail_matches_offset_timestamp(tmp_path: Path) -> None:
    # Reolink-style naming sometimes has JPG at +1s compared to the MP4.
    mp4 = tmp_path / "vooraan_00_20260202163922.mp4"
    jpg = tmp_path / "vooraan_00_20260202163923.jpg"

    mp4.write_bytes(b"dummy")
    jpg.write_bytes(b"jpeg")

    clip = DbClip(
        clip_id="c1",
        camera_id="vooraan",
        abs_path=mp4,
        start_time=datetime(2026, 2, 2, 16, 39, 22, tzinfo=timezone.utc),
        duration_seconds=None,
        size_bytes=mp4.stat().st_size,
        mtime_ns=mp4.stat().st_mtime_ns,
    )

    found = clip.find_thumbnail()
    assert found is not None
    assert found.name == jpg.name


def test_find_thumbnail_ignores_zero_byte(tmp_path: Path) -> None:
    mp4 = tmp_path / "vooraan_00_20260202170808.mp4"
    jpg = tmp_path / "vooraan_00_20260202170808.jpg"

    mp4.write_bytes(b"dummy")
    jpg.write_bytes(b"")

    clip = DbClip(
        clip_id="c2",
        camera_id="vooraan",
        abs_path=mp4,
        start_time=datetime(2026, 2, 2, 17, 8, 8, tzinfo=timezone.utc),
        duration_seconds=None,
        size_bytes=mp4.stat().st_size,
        mtime_ns=mp4.stat().st_mtime_ns,
    )

    assert clip.find_thumbnail() is None
