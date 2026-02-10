from __future__ import annotations

import argparse
import os
import time
from ftplib import FTP
from pathlib import Path

from dotenv import dotenv_values


def _read_env(repo_root: Path) -> dict[str, str]:
    env_path = repo_root / ".env"
    if not env_path.exists() or not env_path.is_file():
        return dict(os.environ)

    raw = dotenv_values(env_path)
    merged: dict[str, str] = {}
    for k, v in raw.items():
        if not k:
            continue
        merged[str(k)] = "" if v is None else str(v)

    # Let process env override .env
    for k, v in os.environ.items():
        merged[k] = v

    return merged


def _ensure_sample_file(path: Path, size_bytes: int) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(os.urandom(size_bytes))


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    env = _read_env(repo_root)

    ap = argparse.ArgumentParser(description="Upload a file to the local FTP prototype")
    ap.add_argument("--host", default=env.get("FTP_HOST") or env.get("FTP_PUBLIC_HOST") or "127.0.0.1")
    ap.add_argument("--port", type=int, default=int(env.get("FTP_PORT") or "21"))
    ap.add_argument("--username", default=env.get("FTP_USERNAME") or "")
    ap.add_argument("--password", default=env.get("FTP_PASSWORD") or "")
    ap.add_argument(
        "--file",
        dest="file_path",
        default=str(repo_root / "debug" / "sample.mp4"),
        help="Local file to upload (defaults to debug/sample.mp4; created if missing)",
    )
    ap.add_argument(
        "--remote",
        dest="remote_name",
        default=None,
        help="Remote filename (defaults to local filename)",
    )
    ap.add_argument(
        "--active",
        action="store_true",
        help="Use active mode (default is passive). Most cameras use passive.",
    )

    args = ap.parse_args()

    if not args.username or not args.password:
        raise SystemExit(
            "Missing FTP credentials. Set FTP_USERNAME/FTP_PASSWORD in .env or pass --username/--password."
        )

    local_path = Path(args.file_path).expanduser().resolve()
    _ensure_sample_file(local_path, size_bytes=512 * 1024)  # 512KB

    remote_name = args.remote_name or local_path.name

    print(f"Connecting to FTP {args.host}:{args.port} as {args.username} (passive={not args.active})")
    with FTP() as ftp:
        deadline = time.time() + 15
        last_err: Exception | None = None

        while True:
            try:
                ftp.connect(args.host, args.port, timeout=10)
                break
            except (ConnectionRefusedError, TimeoutError, EOFError, OSError) as e:
                last_err = e
                if time.time() >= deadline:
                    raise SystemExit(f"Failed to connect to FTP {args.host}:{args.port}: {last_err}")
                time.sleep(0.5)

        ftp.login(args.username, args.password)
        ftp.set_pasv(not args.active)

        with local_path.open("rb") as fp:
            ftp.storbinary(f"STOR {remote_name}", fp)

    print(f"Uploaded {local_path} -> {remote_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
