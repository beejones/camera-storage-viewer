from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values


_CAMERA_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


def _truthy(value: str | None) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class FtpUser:
    camera_id: str
    username: str
    password: str


@dataclass(frozen=True)
class FtpConfig:
    bind_host: str
    port: int
    public_host: str | None
    passive_port_min: int
    passive_port_max: int
    out_dir: Path
    incoming_dir: Path
    spool_dir: Path
    users: list[FtpUser]


def _require_int(name: str, value: str, *, min_value: int, max_value: int) -> int:
    try:
        parsed = int(str(value).strip())
    except ValueError as e:
        raise ValueError(f"{name} must be an integer") from e
    if parsed < min_value or parsed > max_value:
        raise ValueError(f"{name} must be between {min_value} and {max_value}")
    return parsed


def _validate_camera_id(camera_id: str) -> str:
    camera_id = camera_id.strip()
    if not _CAMERA_ID_RE.match(camera_id):
        raise ValueError(
            "camera_id must match ^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$ "
            "(letters, digits, _, -; 1-64 chars)"
        )
    return camera_id


def _parse_users_json(raw: str) -> list[FtpUser]:
    """Parse users config.

    Supported shapes:
    1) List of objects: [{"camera_id": "front", "username": "front", "password": "..."}, ...]
    2) Mapping by camera_id: {"front": {"username": "front", "password": "..."}, ...}
    """

    data = json.loads(raw)

    users: list[FtpUser] = []

    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                raise ValueError("FTP_USERS_JSON list items must be objects")
            camera_id = _validate_camera_id(str(item.get("camera_id", "")).strip())
            username = str(item.get("username", "")).strip()
            password = str(item.get("password", "")).strip()
            if not username or not password:
                raise ValueError("FTP_USERS_JSON entries require username and password")
            users.append(FtpUser(camera_id=camera_id, username=username, password=password))
        return users

    if isinstance(data, dict):
        for camera_id_raw, creds in data.items():
            camera_id = _validate_camera_id(str(camera_id_raw))
            if not isinstance(creds, dict):
                raise ValueError("FTP_USERS_JSON mapping values must be objects")
            username = str(creds.get("username", "")).strip() or camera_id
            password = str(creds.get("password", "")).strip()
            if not password:
                raise ValueError("FTP_USERS_JSON entries require password")
            users.append(FtpUser(camera_id=camera_id, username=username, password=password))
        return users

    raise ValueError("FTP_USERS_JSON must be a list or object")


def load_ftp_config(env: Mapping[str, str] | None = None) -> FtpConfig:
    if env is None:
        # Read dotenv directly (avoids shell exporting which can mangle JSON like FTP_USERS_JSON).
        # Prefer a configurable env path; fall back to historic locations.
        env_path_candidates = [
            Path(str(os.getenv("RUNTIME_ENV_PATH", "")).strip()).expanduser() if os.getenv("RUNTIME_ENV_PATH") else None,
            Path("/app/.env"),
        ]
        file_kv: dict[str, str] = {}
        for candidate in env_path_candidates:
            if not candidate:
                continue
            if candidate.exists():
                raw = dotenv_values(candidate)
                for k, v in raw.items():
                    if not k:
                        continue
                    file_kv[str(k)] = "" if v is None else str(v)
                break

        merged = dict(file_kv)
        merged.update(os.environ)
        env = merged

    bind_host = str(env.get("FTP_BIND_HOST", "0.0.0.0")).strip() or "0.0.0.0"
    port = _require_int("FTP_PORT", str(env.get("FTP_PORT", "21")), min_value=1, max_value=65535)

    public_host = str(env.get("FTP_PUBLIC_HOST", "")).strip() or None

    passive_port_min = _require_int(
        "FTP_PASSIVE_PORT_MIN", str(env.get("FTP_PASSIVE_PORT_MIN", "50000")), min_value=1, max_value=65535
    )
    passive_port_max = _require_int(
        "FTP_PASSIVE_PORT_MAX", str(env.get("FTP_PASSIVE_PORT_MAX", "50100")), min_value=1, max_value=65535
    )
    if passive_port_max < passive_port_min:
        raise ValueError("FTP_PASSIVE_PORT_MAX must be >= FTP_PASSIVE_PORT_MIN")

    out_dir = Path(str(env.get("OUT_DIR", "/data")).strip() or "/data")
    incoming_dir = Path(str(env.get("FTP_INCOMING_DIR", out_dir / "incoming"))).expanduser()
    spool_dir = Path(str(env.get("FTP_SPOOL_DIR", out_dir / "spool"))).expanduser()

    users: list[FtpUser] = []

    users_json = str(env.get("FTP_USERS_JSON", "")).strip()
    if users_json:
        users = _parse_users_json(users_json)
    else:
        username = str(env.get("FTP_USERNAME", "")).strip()
        password = str(env.get("FTP_PASSWORD", "")).strip()
        camera_id = str(env.get("FTP_CAMERA_ID", "camera1")).strip() or "camera1"
        if username and password:
            users = [FtpUser(camera_id=_validate_camera_id(camera_id), username=username, password=password)]

    if not users:
        if _truthy(str(env.get("FTP_DEV_DEFAULTS", ""))):
            users = [FtpUser(camera_id="camera1", username="camera1", password="camera1")]
        else:
            raise ValueError(
                "No FTP users configured. Set FTP_USERS_JSON or set FTP_USERNAME + FTP_PASSWORD (and optional FTP_CAMERA_ID). "
                "For local development only, you can set FTP_DEV_DEFAULTS=true to use a temporary camera1/camera1 credential."
            )

    return FtpConfig(
        bind_host=bind_host,
        port=port,
        public_host=public_host,
        passive_port_min=passive_port_min,
        passive_port_max=passive_port_max,
        out_dir=out_dir,
        incoming_dir=incoming_dir,
        spool_dir=spool_dir,
        users=users,
    )
