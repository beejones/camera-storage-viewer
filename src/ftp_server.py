from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

from src.config import load_ftp_config
from src.ftp_path_rewrite import rewrite_camera_absolute_path


_LOG = logging.getLogger("src.ftp")


class _ViewerFTPHandler(FTPHandler):
    """FTP handler with a couple camera-friendly behaviors.

    Some cameras attempt to `CWD` into date-based folders but do not create them first.
    Also, partial/failed uploads can leave 0-byte files behind on Azure Files.
    """

    # Configurable via FTP_REWRITE_CAMERA_ABSOLUTE_PATHS.
    rewrite_camera_absolute_paths: bool = True

    def _camera_id_for_session(self) -> str | None:
        try:
            root = getattr(self.fs, "root", None)
            if not root:
                return None
            return os.path.basename(os.path.normpath(str(root)))
        except Exception:
            return None

    def _rewrite_ftp_path_if_needed(self, ftp_path: str) -> str:
        if not self.rewrite_camera_absolute_paths:
            return ftp_path
        camera_id = self._camera_id_for_session()
        if not camera_id:
            return ftp_path

        rewritten = rewrite_camera_absolute_path(ftp_path=ftp_path, camera_id=camera_id)
        if rewritten != ftp_path:
            _LOG.info(
                "Rewrote camera upload path: user=%s camera_id=%s from=%s to=%s",
                getattr(self, "username", "?"),
                camera_id,
                ftp_path,
                rewritten,
            )
        return rewritten

    def pre_process_command(self, line: str, cmd: str, arg: str | None) -> None:
        # Rewrite the *FTP* argument before pyftpdlib converts it into a filesystem path.
        # This is the safest way to prevent nested `incoming/<camera>/data/...` trees.
        try:
            if arg and cmd in {"CWD", "XCWD", "MKD", "XMKD", "STOR", "APPE"}:
                # Only touch path-like args (avoid things like LIST -la).
                if arg.startswith("/") or arg.startswith("data") or arg.startswith("incoming"):
                    new_arg = self._rewrite_ftp_path_if_needed(arg)
                    if new_arg != arg:
                        arg = new_arg
                        # Keep logline consistent for common single-word commands.
                        line = f"{cmd} {arg}"
        except Exception:
            pass

        return super().pre_process_command(line, cmd, arg)

    def ftp_CWD(self, path: str) -> None:
        try:
            # pyftpdlib typically passes a filesystem path here (already ftp2fs'd).
            if path and not os.path.isdir(path):
                os.makedirs(path, exist_ok=True)
        except Exception:
            # Fall back to default behavior (will respond 550 if invalid)
            pass
        return super().ftp_CWD(path)

    def ftp_STOR(self, file: str, mode: str = "w") -> None:
        try:
            parent = os.path.dirname(file)
            if parent and not os.path.isdir(parent):
                os.makedirs(parent, exist_ok=True)
        except Exception:
            # Default handler will deal with errors.
            pass
        return super().ftp_STOR(file, mode)

    def on_incomplete_file_received(self, file: str) -> None:
        # pyftpdlib calls this when the client closes early / transfer fails.
        # Delete the partial file so we don't accumulate confusing 0-byte entries.
        try:
            if file and os.path.exists(file):
                os.remove(file)
                _LOG.info("Removed incomplete upload: %s", file)
        except Exception:
            _LOG.warning("Failed to remove incomplete upload: %s", file)



def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def serve() -> None:
    cfg = load_ftp_config()

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    _ensure_dir(cfg.out_dir)
    _ensure_dir(cfg.incoming_dir)
    _ensure_dir(cfg.spool_dir)

    _LOG.info(
        "FTP config: bind=%s:%s public_host=%s passive_ports=%s-%s permit_foreign_addresses=%s rewrite_camera_absolute_paths=%s out_dir=%s incoming_dir=%s spool_dir=%s",
        cfg.bind_host,
        cfg.port,
        cfg.public_host or "(none)",
        cfg.passive_port_min,
        cfg.passive_port_max,
        cfg.permit_foreign_addresses,
        cfg.rewrite_camera_absolute_paths,
        cfg.out_dir,
        cfg.incoming_dir,
        cfg.spool_dir,
    )

    authorizer = DummyAuthorizer()

    for user in cfg.users:
        homedir = cfg.incoming_dir / user.camera_id
        _ensure_dir(homedir)

        # Full permissions for MVP. Tighten later if needed.
        authorizer.add_user(
            username=user.username,
            password=user.password,
            homedir=str(homedir),
            perm="elradfmwMT",
        )
        _LOG.info("Configured FTP user '%s' for camera_id='%s' homedir='%s'", user.username, user.camera_id, homedir)

    handler = _ViewerFTPHandler
    handler.authorizer = authorizer

    handler.rewrite_camera_absolute_paths = bool(cfg.rewrite_camera_absolute_paths)

    handler.passive_ports = range(cfg.passive_port_min, cfg.passive_port_max + 1)

    # Allow data connections from a different source IP than the control connection.
    # This is commonly needed behind NAT/load balancers (and observed with some cameras).
    handler.permit_foreign_addresses = bool(cfg.permit_foreign_addresses)
    if cfg.permit_foreign_addresses:
        _LOG.info("permit_foreign_addresses enabled (accept data connections from different source IP)")

    if cfg.public_host:
        handler.masquerade_address = cfg.public_host
        _LOG.info("Using masquerade_address (PASV public host): %s", cfg.public_host)

    address = (cfg.bind_host, cfg.port)
    _LOG.info(
        "Starting FTP server on %s:%s (passive ports %s-%s)",
        cfg.bind_host,
        cfg.port,
        cfg.passive_port_min,
        cfg.passive_port_max,
    )

    server = FTPServer(address, handler)
    server.serve_forever(timeout=1, blocking=True, handle_exit=True)


if __name__ == "__main__":
    serve()
