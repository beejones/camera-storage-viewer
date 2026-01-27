from __future__ import annotations

import logging
import os
from pathlib import Path

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

from src.config import load_ftp_config


_LOG = logging.getLogger("src.ftp")


class _ViewerFTPHandler(FTPHandler):
    """FTP handler with a couple camera-friendly behaviors.

    Some cameras attempt to `CWD` into date-based folders but do not create them first.
    Also, partial/failed uploads can leave 0-byte files behind on Azure Files.
    """

    def ftp_CWD(self, path: str) -> None:
        try:
            fs_path = self.fs.ftp2fs(path)
            if fs_path and not os.path.isdir(fs_path):
                os.makedirs(fs_path, exist_ok=True)
        except Exception:
            # Fall back to default behavior (will respond 550 if invalid)
            pass
        return super().ftp_CWD(path)

    def ftp_STOR(self, file: str, mode: str = "w") -> None:
        try:
            fs_path = self.fs.ftp2fs(file)
            parent = os.path.dirname(fs_path)
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
        "FTP config: bind=%s:%s public_host=%s passive_ports=%s-%s permit_foreign_addresses=%s out_dir=%s incoming_dir=%s spool_dir=%s",
        cfg.bind_host,
        cfg.port,
        cfg.public_host or "(none)",
        cfg.passive_port_min,
        cfg.passive_port_max,
        cfg.permit_foreign_addresses,
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
