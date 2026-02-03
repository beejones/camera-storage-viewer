from __future__ import annotations

from src.ftp_path_rewrite import rewrite_camera_absolute_path


def test_rewrite_camera_absolute_path_data_incoming_prefix() -> None:
    assert (
        rewrite_camera_absolute_path(ftp_path="/data/incoming/vooraan/2026/02/02/clip.mp4", camera_id="vooraan")
        == "/2026/02/02/clip.mp4"
    )


def test_rewrite_camera_absolute_path_incoming_prefix() -> None:
    assert (
        rewrite_camera_absolute_path(ftp_path="incoming/vooraan/2026/02/02/clip.mp4", camera_id="vooraan")
        == "/2026/02/02/clip.mp4"
    )


def test_rewrite_camera_absolute_path_bare_data_dir() -> None:
    assert rewrite_camera_absolute_path(ftp_path="/data", camera_id="vooraan") == "/"
    assert rewrite_camera_absolute_path(ftp_path="/data/", camera_id="vooraan") == "/"
    assert rewrite_camera_absolute_path(ftp_path="data", camera_id="vooraan") == "/"
    assert rewrite_camera_absolute_path(ftp_path="data/", camera_id="vooraan") == "/"


def test_rewrite_camera_absolute_path_no_match() -> None:
    assert rewrite_camera_absolute_path(ftp_path="/2026/02/02/clip.mp4", camera_id="vooraan") == "/2026/02/02/clip.mp4"
    assert rewrite_camera_absolute_path(ftp_path="data/incoming/other/clip.mp4", camera_id="vooraan") == "data/incoming/other/clip.mp4"
