from __future__ import annotations

import os
import time
from pathlib import Path

from src.ingest import ingest_incoming_once


def test_ingest_moves_old_videos_and_sidecars(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    incoming = out_dir / "incoming" / "front"
    incoming.mkdir(parents=True)

    video = incoming / "clip1.mp4"
    thumb = incoming / "clip1.jpg"

    video.write_bytes(b"dummy")
    thumb.write_bytes(b"thumb")

    # Make the file look "old enough".
    old = time.time() - 3600
    os.utime(video, (old, old))
    os.utime(thumb, (old, old))

    res = ingest_incoming_once(out_dir=out_dir, min_age_seconds=60, apply=True)
    assert res.moved == 1

    # Should now be under videos/<camera_id>/YYYY/MM/DD
    videos_root = out_dir / "videos" / "front"
    found = list(videos_root.rglob("clip1.mp4"))
    assert len(found) == 1
    dest_video = found[0]
    assert dest_video.exists()
    assert dest_video.with_suffix(".jpg").exists()
