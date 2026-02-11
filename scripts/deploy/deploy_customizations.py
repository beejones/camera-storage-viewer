"""Repo-specific deployment hooks for the protected-azure-container engine.

The upstream engine loads hooks in this precedence order:
1) --hooks-module <path>
2) DEPLOY_HOOKS_MODULE=<path>
3) scripts/deploy/deploy_customizations.py (this file)

See upstream hook reference:
- https://github.com/beejones/protected-azure-container/blob/main/docs/deploy/HOOKS.md

camera-storage-viewer uses additional runtime keys (FTP_* etc). Historically we
worked around upstream strict validation by temporarily rewriting `.env` and
`.env.deploy` and keeping `*.full` backup copies.

We no longer do that: `.env` and `.env.deploy` are the only source-of-truth
files, and the upstream wrapper extends the upstream schema at runtime so strict
validation accepts viewer-specific keys without file rewriting.
"""

from __future__ import annotations

from typing import Any, Protocol


def _env_view(ctx: Any) -> dict[str, Any]:
    env = getattr(ctx, "env", None)
    if env is None:
        raise TypeError("Expected deploy ctx with .env")
    return env


def _args_view(ctx: Any) -> Any:
    return getattr(ctx, "args", None)


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

        # Defaults for this repo.
        env.setdefault("OUT_DIR", "/data")
        env.setdefault("WEB_PORT", "8081")

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
