"""Repo-specific deployment hooks for the protected-azure-container engine.

The upstream engine loads hooks in this precedence order:
1) --hooks-module <path>
2) DEPLOY_HOOKS_MODULE=<path>
3) scripts/deploy/deploy_customizations.py (this file)

See upstream hook reference:
- https://github.com/beejones/protected-azure-container/blob/main/docs/deploy/HOOKS.md

camera-storage-viewer uses additional runtime keys (FTP_* etc). Upstream validates
repo-root `.env` against a strict schema, so we treat `.env` as the source of
truth and temporarily slim it for validation while preserving the full content
for upload/deploy.
"""

from __future__ import annotations

import atexit
from pathlib import Path
from typing import Any, Protocol


# Upstream PR #18 splits secrets into `.env.secrets`.
# Keep `.env` slimmed to only upstream runtime keys during strict validation.
_UPSTREAM_RUNTIME_KEYS = {"BASIC_AUTH_USER"}


def _env_view(ctx: Any) -> dict[str, Any]:
    env = getattr(ctx, "env", None)
    if env is None:
        raise TypeError("Expected deploy ctx with .env")
    return env


def _args_view(ctx: Any) -> Any:
    return getattr(ctx, "args", None)


def _repo_root(ctx: Any) -> Path:
    rr = getattr(ctx, "repo_root", None)
    if rr is None:
        raise TypeError("Expected deploy ctx with .repo_root")
    return Path(rr)


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


def _write_slim_dotenv(path: Path, kv: dict[str, str], header_lines: list[str]) -> None:
    lines = list(header_lines)
    lines.append("")
    for k in sorted(kv.keys()):
        lines.append(f"{k}={kv[k]}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _comment_out_unknown_keys(text: str, *, keys_to_comment: set[str]) -> str:
    lines: list[str] = []
    for raw in text.splitlines():
        stripped = raw.lstrip()
        if not stripped or stripped.startswith("#"):
            lines.append(raw)
            continue

        line = stripped
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()

        if "=" not in line:
            lines.append(raw)
            continue

        key = line.split("=", 1)[0].strip()
        if key in keys_to_comment:
            lines.append(f"# {raw}")
        else:
            lines.append(raw)

    # Preserve trailing newline for nicer diffs / editor UX.
    return "\n".join(lines) + "\n"


def _merge_dotenv_text(base_text: str, updates: dict[str, str]) -> str:
    # Best-effort: update existing assignments in-place, and append missing keys.
    # Preserves comments and unknown keys from the base text.
    remaining = dict(updates)
    out_lines: list[str] = []

    for raw in base_text.splitlines():
        stripped = raw.lstrip()
        if not stripped or stripped.startswith("#"):
            out_lines.append(raw)
            continue

        leading = raw[: len(raw) - len(stripped)]
        export_prefix = ""
        line = stripped
        if line.startswith("export "):
            export_prefix = "export "
            line = line[len("export ") :].lstrip()

        if "=" not in line:
            out_lines.append(raw)
            continue

        key, _value = line.split("=", 1)
        key = key.strip()
        if key in remaining:
            out_lines.append(f"{leading}{export_prefix}{key}={remaining.pop(key)}")
        else:
            out_lines.append(raw)

    if remaining:
        if out_lines and out_lines[-1].strip():
            out_lines.append("")
        for k in sorted(remaining.keys()):
            out_lines.append(f"{k}={remaining[k]}")

    return "\n".join(out_lines) + "\n"


def _aci_port_limit_validate(*, ftp_port: int, passive_min: int, passive_max: int) -> None:
    if ftp_port < 1 or ftp_port > 65535:
        raise ValueError(f"FTP_PORT must be 1-65535, got {ftp_port}")
    if passive_min < 1 or passive_min > 65535:
        raise ValueError(f"FTP_PASSIVE_PORT_MIN must be 1-65535, got {passive_min}")
    if passive_max < 1 or passive_max > 65535:
        raise ValueError(f"FTP_PASSIVE_PORT_MAX must be 1-65535, got {passive_max}")
    if passive_max < passive_min:
        raise ValueError("FTP_PASSIVE_PORT_MAX must be >= FTP_PASSIVE_PORT_MIN")

    unique_ports = {ftp_port, *range(passive_min, passive_max + 1)}
    if len(unique_ports) > 5:
        raise ValueError(
            "ACI container groups support at most 5 public ports; "
            f"requested {len(unique_ports)} ports. "
            "Reduce FTP_PASSIVE_PORT_MAX so the passive range is <= 4 ports "
            "(e.g. 50000-50003)."
        )


class _HooksProto(Protocol):
    def pre_validate_env(self, ctx: Any) -> None: ...

    def post_validate_env(self, ctx: Any) -> None: ...

    def build_deploy_plan(self, ctx: Any, plan: Any) -> None: ...

    def post_deploy(self, ctx: Any, plan: Any, result: Any) -> None: ...


class CameraStorageViewerHooks:
    def pre_validate_env(self, ctx: Any) -> None:
        env = _env_view(ctx)
        args = _args_view(ctx)
        repo_root = _repo_root(ctx)

        # Defaults for this repo.
        env.setdefault("OUT_DIR", "/data")
        env.setdefault("WEB_PORT", "8081")

        # Ensure additional runtime keys (FTP_*) are available in env even if
        # upstream's strict validation slims the .env file.
        runtime_path = repo_root / ".env"
        full_path = repo_root / ".env.full"

        deploy_env_path = repo_root / ".env.deploy"
        deploy_full_path = repo_root / ".env.deploy.full"

        if runtime_path.exists():
            original_text = runtime_path.read_text(encoding="utf-8")
            full_kv = _parse_dotenv_file(runtime_path)

            # Make all keys available via ctx.env.
            for k, v in full_kv.items():
                if k in _UPSTREAM_RUNTIME_KEYS:
                    env[k] = v
                else:
                    env.setdefault(k, v)

            full_path.write_text(original_text, encoding="utf-8")

            slim_kv = {k: v for k, v in full_kv.items() if k in _UPSTREAM_RUNTIME_KEYS}
            _write_slim_dotenv(
                runtime_path,
                slim_kv,
                header_lines=[
                    "# TEMPORARY during deploy: slimmed for upstream strict validation.",
                    "# Source of truth is still this repo's .env; it will be restored on exit.",
                ],
            )

            def _restore_env() -> None:
                try:
                    if full_path.exists():
                        runtime_path.write_text(full_path.read_text(encoding="utf-8"), encoding="utf-8")
                        full_path.unlink(missing_ok=True)
                except Exception:
                    return

            atexit.register(_restore_env)

        # Deploy-time file: upstream validates `.env.deploy` strictly and rejects unknown keys.
        # This repo may carry extra app-specific keys there (e.g. FTP_CPU_CORES) which upstream
        # doesn't know. If present, temporarily comment them out for validation, but restore the
        # original file on exit while merging any upstream write-backs (AZURE_* IDs, etc).
        if deploy_env_path.exists():
            try:
                deploy_text = deploy_env_path.read_text(encoding="utf-8")
            except Exception:
                deploy_text = ""

            deploy_kv = _parse_dotenv_file(deploy_env_path)

            keys_to_comment: set[str] = set()
            # Known viewer-only keys that have shown up in `.env.deploy` and are rejected by
            # upstream strict validation.
            keys_to_comment |= {k for k in ("FTP_CPU_CORES", "FTP_MEMORY_GB") if k in deploy_kv}

            try:
                from env_schema import DEPLOY_SCHEMA  # type: ignore

                allowed = {spec.key.value for spec in DEPLOY_SCHEMA}
                keys_to_comment |= {k for k in deploy_kv.keys() if k not in allowed}
            except Exception:
                # Conservative fallback: FTP_* are typically runtime/app keys, not deploy schema keys.
                keys_to_comment |= {k for k in deploy_kv.keys() if k.startswith("FTP_")}

            if keys_to_comment:
                # Ensure values are still available via ctx.env during the run.
                for k in sorted(keys_to_comment):
                    env.setdefault(k, deploy_kv.get(k, ""))

                if not deploy_full_path.exists():
                    deploy_full_path.write_text(deploy_text, encoding="utf-8")

                deploy_env_path.write_text(
                    _comment_out_unknown_keys(deploy_text, keys_to_comment=keys_to_comment),
                    encoding="utf-8",
                )

                def _restore_deploy_env() -> None:
                    try:
                        if not deploy_full_path.exists():
                            return
                        base_text = deploy_full_path.read_text(encoding="utf-8")
                        current_kv = _parse_dotenv_file(deploy_env_path)
                        merged = _merge_dotenv_text(base_text, current_kv)
                        deploy_env_path.write_text(merged, encoding="utf-8")
                        deploy_full_path.unlink(missing_ok=True)
                    except Exception:
                        return

                atexit.register(_restore_deploy_env)

        # Prefer uploading the full runtime env file content. This repo keeps
        # runtime secrets separate in `.env.secrets`, so uploading `.env` raw is
        # expected and avoids accidentally dropping viewer-specific keys.
        if args is not None and hasattr(args, "upload_env_raw"):
            if not bool(getattr(args, "upload_env_raw")):
                setattr(args, "upload_env_raw", True)

    def post_validate_env(self, ctx: Any) -> None:
        env = _env_view(ctx)

        def _int(name: str, default: int) -> int:
            raw = str(env.get(name, "") or "").strip()
            if not raw:
                return default
            return int(raw)

        ftp_port = _int("FTP_PORT", 21)
        passive_min = _int("FTP_PASSIVE_PORT_MIN", 50000)
        passive_max = _int("FTP_PASSIVE_PORT_MAX", 50003)
        _aci_port_limit_validate(ftp_port=ftp_port, passive_min=passive_min, passive_max=passive_max)

    def build_deploy_plan(self, _ctx: Any, _plan: Any) -> None:
        ctx = _ctx
        plan = _plan
        env = _env_view(ctx)
        args = _args_view(ctx)

        # Preserve existing engine defaults, but allow the legacy --service style
        # (used by camera-storage-viewer) to override deploy_mode when present.
        service = str(getattr(args, "service", "") or "").strip().lower() if args is not None else ""
        if service:
            setattr(plan, "deploy_mode", service)

        caddy_image = str(env.get("CADDY_IMAGE", "") or "").strip()
        if caddy_image and hasattr(plan, "caddy_image"):
            setattr(plan, "caddy_image", caddy_image)

        web_port_raw = str(env.get("WEB_PORT", "") or "").strip()
        if web_port_raw and hasattr(plan, "app_port"):
            setattr(plan, "app_port", int(web_port_raw))

        pmin = str(env.get("FTP_PASSIVE_PORT_MIN", "") or "").strip()
        pmax = str(env.get("FTP_PASSIVE_PORT_MAX", "") or "").strip()
        if pmin and pmax and hasattr(plan, "ftp_passive_range"):
            setattr(plan, "ftp_passive_range", f"{int(pmin)}-{int(pmax)}")

        return None

    def post_deploy(self, _ctx: Any, _plan: Any, _result: Any) -> None:
        # No-op: deployments are handled by the primary deploy scripts.
        return None


_HOOKS: _HooksProto = CameraStorageViewerHooks()


def get_hooks() -> _HooksProto:
    return _HOOKS
