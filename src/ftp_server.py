from __future__ import annotations

import errno
import logging
import os
from pathlib import Path

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

    rewrite_camera_absolute_paths: bool = True

    def _camera_id_for_session(self) -> str | None:
        try:
            root = getattr(self.fs, "root", None)
            if not root:
                return None
            return os.path.basename(os.path.normpath(str(root)))
        except Exception:
            return None

    def _rewrite_path_if_needed(self, path: str) -> str:
        if not self.rewrite_camera_absolute_paths:
            return path
        camera_id = self._camera_id_for_session()
        if not camera_id:
            return path
        rewritten = rewrite_camera_absolute_path(ftp_path=path, camera_id=camera_id)
        if rewritten != path:
            _LOG.info("Rewrote camera upload path: user=%s camera_id=%s from=%s to=%s", getattr(self, "username", "?"), camera_id, path, rewritten)
        return rewritten

    def ftp_CWD(self, path: str) -> None:
        try:
            path = self._rewrite_path_if_needed(path)
            fs_path = self.fs.ftp2fs(path)
            if fs_path and not os.path.isdir(fs_path):
                os.makedirs(fs_path, exist_ok=True)
        except OSError as e:
            # Some cameras expect the server to create date-based folders.
            # When the underlying storage is full, return a more accurate FTP code.
            if e.errno == errno.ENOSPC:
                _LOG.error("No space left on device while creating directory for CWD: %s", path)
                self.respond("452 No space left on device.")
                return
            # Fall back to default behavior (will respond 550 if invalid)
        except Exception:
            # Fall back to default behavior (will respond 550 if invalid)
            return super().ftp_CWD(path)
        return super().ftp_CWD(path)

    def ftp_STOR(self, file: str, mode: str = "w") -> None:
        try:
            file = self._rewrite_path_if_needed(file)
            fs_path = self.fs.ftp2fs(file)
            parent = os.path.dirname(fs_path)
            if parent and not os.path.isdir(parent):
                os.makedirs(parent, exist_ok=True)
        except OSError as e:
            if e.errno == errno.ENOSPC:
                _LOG.error("No space left on device while creating parent directory for STOR: %s", file)
                self.respond("452 No space left on device.")
                return
            # Default handler will deal with errors.
        except Exception:
            # Default handler will deal with errors.
            return super().ftp_STOR(file, mode)
        return super().ftp_STOR(file, mode)

    def ftp_MKD(self, path: str) -> None:
        """Create directories in a camera-friendly way.

        Cameras often try to create year/month/day directories (sometimes using
        an absolute server directory like `/data/incoming/<camera_id>/...`).
        We rewrite those paths into the jailed root, and treat "already exists"
        as success so the camera continues to `CWD`/`STOR`.
        """

        try:
            path = self._rewrite_path_if_needed(path)
            fs_path = self.fs.ftp2fs(path)
            if fs_path:
                os.makedirs(fs_path, exist_ok=True)

            # Respond success even if it already existed.
            self.respond(f'257 "{path}" directory created.')
            return
        except OSError as e:
            if e.errno == errno.ENOSPC:
                _LOG.error("No space left on device while creating directory for MKD: %s", path)
                self.respond("452 No space left on device.")
                return
            return super().ftp_MKD(path)
        except Exception:
            return super().ftp_MKD(path)

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


def _format_bytes(n: int) -> str:
    step = 1024.0
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    v = float(n)
    for u in units:
        if v < step:
            return f"{v:.1f}{u}"
        v /= step
    return f"{v:.1f}EiB"


def _log_out_dir_space(out_dir: Path) -> None:
    try:
        st = os.statvfs(str(out_dir))
        total = int(st.f_frsize) * int(st.f_blocks)
        free = int(st.f_frsize) * int(st.f_bavail)
        used = max(total - free, 0)
        _LOG.info("OUT_DIR disk: path=%s total=%s used=%s free=%s", out_dir, _format_bytes(total), _format_bytes(used), _format_bytes(free))
    except Exception as e:
        _LOG.info("OUT_DIR disk: path=%s (statvfs unavailable: %s)", out_dir, e)


def serve() -> None:
    cfg = load_ftp_config()

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    _ensure_dir(cfg.out_dir)
    _ensure_dir(cfg.incoming_dir)
    _ensure_dir(cfg.spool_dir)

    _log_out_dir_space(cfg.out_dir)

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
