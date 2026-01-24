from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from src.web_api import app


def test_api_contract_smoke(tmp_path: Path, monkeypatch) -> None:
    out_dir = tmp_path / "out"
    (out_dir / "incoming" / "front").mkdir(parents=True)

    # A dummy mp4 is fine for FileResponse; we are testing the contract and routing.
    clip_path = out_dir / "incoming" / "front" / "clip1.mp4"
    clip_path.write_bytes(b"\x00\x00\x00\x18ftypmp42dummy")

    monkeypatch.setenv("OUT_DIR", str(out_dir))

    client = TestClient(app)

    cameras = client.get("/api/cameras").json()
    assert [c["camera_id"] for c in cameras] == ["front"]

    today = datetime.now(timezone.utc).date().isoformat()
    clips_resp = client.get(f"/api/cameras/front/clips?date={today}")
    assert clips_resp.status_code == 200
    clips = clips_resp.json()
    assert len(clips) == 1
    clip_id = clips[0]["clip_id"]

    detail_resp = client.get(f"/api/clips/{clip_id}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["camera_id"] == "front"

    media_resp = client.get(f"/media/{clip_id}")
    assert media_resp.status_code == 200
    assert media_resp.headers["content-type"].startswith("video/")
