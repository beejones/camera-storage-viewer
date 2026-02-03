from __future__ import annotations

from pyftpdlib.handlers import FTPHandler

from src.ftp_server import _ViewerFTPHandler


def test_viewer_handler_overrides_camera_commands() -> None:
    # Guardrail: path rewriting / camera-friendly behaviors must apply to the
    # FTP commands cameras actually use.
    assert _ViewerFTPHandler.ftp_CWD is not FTPHandler.ftp_CWD
    assert _ViewerFTPHandler.ftp_STOR is not FTPHandler.ftp_STOR
    assert _ViewerFTPHandler.ftp_MKD is not FTPHandler.ftp_MKD
