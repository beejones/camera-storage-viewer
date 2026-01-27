"""camera-storage-viewer deployment customizations via upstream deploy hooks.

This module is auto-loaded by `scripts/deploy/deploy_hooks.py` when present.
Keep viewer-specific behavior here so upstream syncs stay low-conflict.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from scripts.deploy.deploy_hooks import DeployContext, DeployPlan
from scripts.deploy.env_schema import VarsEnum


def _get_arg(args: argparse.Namespace, name: str, default: str | None = None) -> str | None:
    if hasattr(args, name):
        val = getattr(args, name)
        if val is None:
            return default
        s = str(val).strip()
        return s or default
    return default


def _get_env_str(ctx: DeployContext, key: str) -> str | None:
    val = ctx.env.get(key)
    s = str(val or "").strip()
    return s or None


def _get_env_int(ctx: DeployContext, key: str, *, default: int | None = None) -> int | None:
    raw = _get_env_str(ctx, key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{key} must be an int, got {raw!r}")


def _aci_port_limit_validate(*, ftp_port: int, passive_min: int, passive_max: int) -> None:
    if ftp_port < 1 or ftp_port > 65535:
        raise ValueError(f"FTP_PORT must be 1-65535, got {ftp_port}")
    if passive_min < 1 or passive_min > 65535:
        raise ValueError(f"FTP_PASSIVE_PORT_MIN must be 1-65535, got {passive_min}")
    if passive_max < 1 or passive_max > 65535:
        raise ValueError(f"FTP_PASSIVE_PORT_MAX must be 1-65535, got {passive_max}")
    if passive_max < passive_min:
        raise ValueError("FTP_PASSIVE_PORT_MAX must be >= FTP_PASSIVE_PORT_MIN")

    # Azure Container Instances limitation: maximum 5 public IP ports per container group.
    # FTP requires 1 control port + N passive ports.
    unique_ports = {ftp_port, *range(passive_min, passive_max + 1)}
    if len(unique_ports) > 5:
        raise ValueError(
            "ACI container groups support at most 5 public ports; "
            f"requested {len(unique_ports)} ports. "
            "Reduce FTP_PASSIVE_PORT_MAX so the passive range is <= 4 ports "
            "(e.g. 50000-50003), or deploy somewhere that supports larger port ranges."
        )


@dataclass
class _ViewerHooks:
    """Viewer-specific hook implementation.

    All hooks are optional; upstream safely no-ops missing methods.
    """

    def pre_validate_env(self, ctx: DeployContext) -> None:
        # Keep this conservative: only set viewer defaults when missing.
        ctx.env.setdefault(VarsEnum.OUT_DIR.value, "/data")
        ctx.env.setdefault(VarsEnum.WEB_PORT.value, "8081")

    def post_validate_env(self, ctx: DeployContext) -> None:
        service = _get_arg(ctx.args, "service")

        # Only enforce FTP port-range limits when FTP is in play.
        # (This is mainly to prevent confusing ACI failures when users widen the passive range.)
        if service not in {"ftp", "full", None, ""}:
            return

        ftp_port = _get_env_int(ctx, VarsEnum.FTP_PORT.value, default=21) or 21
        passive_min = _get_env_int(ctx, VarsEnum.FTP_PASSIVE_PORT_MIN.value, default=50000) or 50000
        passive_max = _get_env_int(ctx, VarsEnum.FTP_PASSIVE_PORT_MAX.value, default=50003) or 50003

        _aci_port_limit_validate(ftp_port=ftp_port, passive_min=passive_min, passive_max=passive_max)

    def build_deploy_plan(self, ctx: DeployContext, plan: DeployPlan) -> None:
        # If running camera-storage-viewer deploy scripts, we can infer mode from args.
        service = _get_arg(ctx.args, "service")
        if service:
            plan.deploy_mode = service

        # Expose viewer-ish metadata to downstream deploy logic.
        if hasattr(plan, "service_mode"):
            plan.service_mode = "app" if (service in {None, "", "web", "web-caddy", "full"}) else service

        # Prefer an explicit caddy image if set in env (CI / .env.deploy).
        caddy_image = _get_env_str(ctx, VarsEnum.CADDY_IMAGE.value)
        if caddy_image:
            plan.caddy_image = caddy_image

        # When deploying the web container, the *internal* port is WEB_PORT.
        # (Public ports are handled by the container group and/or caddy sidecar.)
        if service in {"web", "web-caddy", "full"}:
            web_port = _get_env_int(ctx, VarsEnum.WEB_PORT.value, default=None)
            if web_port:
                plan.app_port = web_port

        # Provide ftp passive range as a stable string for YAML generators.
        ftp_min = _get_env_int(ctx, VarsEnum.FTP_PASSIVE_PORT_MIN.value, default=None)
        ftp_max = _get_env_int(ctx, VarsEnum.FTP_PASSIVE_PORT_MAX.value, default=None)
        if ftp_min is not None and ftp_max is not None and hasattr(plan, "ftp_passive_range"):
            plan.ftp_passive_range = f"{ftp_min}-{ftp_max}"


def get_hooks() -> _ViewerHooks:
    return _ViewerHooks()
