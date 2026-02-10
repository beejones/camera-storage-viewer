#!/usr/bin/env python3
"""Wrapper for the upstream GitHub Actions env sync script.

Upstream engine is vendored as a git submodule at:
  scripts/deploy/_upstream

This wrapper exists because camera-storage-viewer uses additional runtime keys
(e.g. FTP_*) that upstream strict validation would reject.

Approach:
- Create temporary slimmed env files for upstream validation/sync.
- After upstream finishes, override RUNTIME_ENV_DOTENV with the FULL `.env`
  (so FTP_* and other app keys are present for CI deploys).
"""

from __future__ import annotations

import atexit
import subprocess
import sys
import tempfile
from pathlib import Path


_UPSTREAM_RUNTIME_KEYS = {"BASIC_AUTH_USER", "BASIC_AUTH_HASH", "APP_SECRET"}

# Minimal legacy mapping for deploy-time keys (extend if you have older key names).
_LEGACY_MAP = {
    "GHCR_IMAGE": "APP_IMAGE",
    "IMAGE": "APP_IMAGE",
    "AZURE_PUBLIC_DOMAIN": "PUBLIC_DOMAIN",
    "AZURE_ACME_EMAIL": "ACME_EMAIL",
    "AZURE_CPU": "APP_CPU_CORES",
    "AZURE_MEMORY_GB": "APP_MEMORY_GB",
}


def _parse_dotenv_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def _write_dotenv(path: Path, kv: dict[str, str], header: str) -> None:
    lines = [header, ""]
    for k in sorted(kv.keys()):
        lines.append(f"{k}={kv[k]}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _argv_has_flag(argv: list[str], flag: str) -> bool:
    return flag in argv or any(a.startswith(flag + "=") for a in argv)


def _is_dry_run(argv: list[str]) -> bool:
    # upstream uses --set/--no-set
    if "--no-set" in argv:
        return True
    if "--set" in argv:
        return False
    # Default: upstream generally sets by default; treat as set.
    return False


def main(argv: list[str] | None = None, repo_root_override: Path | None = None) -> None:
    repo_root = repo_root_override or Path(__file__).resolve().parents[2]
    upstream_deploy_dir = repo_root / "scripts" / "deploy" / "_upstream" / "scripts" / "deploy"

    if not upstream_deploy_dir.exists():
        raise SystemExit(
            "Upstream deploy engine submodule not present. "
            "Run: git submodule update --init --recursive"
        )

    argv_list = list(argv if argv is not None else sys.argv[1:])

    runtime_path = repo_root / ".env"
    deploy_path = repo_root / ".env.deploy"

    runtime_kv = _parse_dotenv_file(runtime_path)
    deploy_kv = _parse_dotenv_file(deploy_path)

    slim_runtime_kv = {k: v for k, v in runtime_kv.items() if k in _UPSTREAM_RUNTIME_KEYS}

    translated_deploy_kv: dict[str, str] = {}
    for k, v in deploy_kv.items():
        new_key = _LEGACY_MAP.get(k, k)
        if new_key not in translated_deploy_kv:
            translated_deploy_kv[new_key] = v

    temp_dir = Path(tempfile.mkdtemp(prefix="gh_sync_"))
    slim_runtime_path = temp_dir / ".env"
    slim_deploy_path = temp_dir / ".env.deploy"

    _write_dotenv(
        slim_runtime_path,
        slim_runtime_kv,
        header="# TEMPORARY during sync: slimmed for upstream strict validation.",
    )
    _write_dotenv(
        slim_deploy_path,
        translated_deploy_kv,
        header="# TEMPORARY during sync: translated+slimmed for upstream strict validation.",
    )

    def _cleanup() -> None:
        try:
            slim_runtime_path.unlink(missing_ok=True)
            slim_deploy_path.unlink(missing_ok=True)
            temp_dir.rmdir()
        except Exception:
            pass

    atexit.register(_cleanup)

    # Call upstream with slimmed files by injecting/overriding --runtime-env/--deploy-env.
    new_argv: list[str] = ["--runtime-env", str(slim_runtime_path), "--deploy-env", str(slim_deploy_path)]
    skip_next = False
    for a in argv_list:
        if skip_next:
            skip_next = False
            continue
        if a in ("--runtime-env", "--deploy-env"):
            skip_next = True
            continue
        if a.startswith("--runtime-env=") or a.startswith("--deploy-env="):
            continue
        new_argv.append(a)

    sys.path.insert(0, str(upstream_deploy_dir))
    import gh_sync_actions_env as upstream_script  # type: ignore

    sys.argv = [sys.argv[0]] + new_argv
    upstream_script.main()

    # Override runtime secret with FULL .env so camera-storage-viewer keys (FTP_*) are included.
    if runtime_path.exists() and not _is_dry_run(argv_list):
        runtime_text = runtime_path.read_text(encoding="utf-8")
        try:
            subprocess.run(
                ["gh", "secret", "set", "RUNTIME_ENV_DOTENV", "-b", runtime_text],
                check=True,
                text=True,
                capture_output=True,
            )
            print("[ok] set secret RUNTIME_ENV_DOTENV (full .env content)")
        except FileNotFoundError:
            print("[warn] gh CLI not found; could not set RUNTIME_ENV_DOTENV")
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "").strip()
            raise SystemExit(f"Failed to set RUNTIME_ENV_DOTENV via gh: {stderr}")


if __name__ == "__main__":
    main(sys.argv[1:])
