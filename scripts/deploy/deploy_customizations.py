"""camera-storage-viewer deployment customizations via upstream deploy hooks.

This module is auto-loaded by `scripts/deploy/deploy_hooks.py` when present.
Keep viewer-specific behavior here so upstream syncs stay low-conflict.
"""

from __future__ import annotations

import argparse
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from scripts.deploy.deploy_hooks import DeployContext, DeployPlan
from scripts.deploy.env_schema import VarsEnum
from scripts.deploy.azure_utils import run_az_command
from scripts.deploy import docker_compose_helpers as compose_helpers
from scripts.deploy import csv_deploy_yaml_helpers as ftp_yaml_helpers


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


def _delete_container_group(*, rg: str, name: str) -> None:
    """Delete an ACI container group and wait for full deletion.

    ACI is eventually consistent; waiting avoids `Conflict` on recreate.
    """

    run_az_command(
        ["container", "delete", "--resource-group", rg, "--name", name, "--yes"],
        capture_output=False,
        ignore_errors=True,
    )

    max_wait = 180
    poll_interval = 5
    waited = 0
    while waited < max_wait:
        result = run_az_command(
            [
                "container",
                "show",
                "--resource-group",
                rg,
                "--name",
                name,
                "--query",
                "provisioningState",
                "-o",
                "tsv",
            ],
            capture_output=True,
            ignore_errors=True,
            verbose=False,
        )
        if result is None:
            return
        time.sleep(poll_interval)
        waited += poll_interval


@dataclass
class _ViewerHooks:
    """Viewer-specific hook implementation.

    All hooks are optional; upstream safely no-ops missing methods.
    """

    def pre_validate_env(self, ctx: DeployContext) -> None:
        # Keep this conservative: only set viewer defaults when missing.
        ctx.env.setdefault(VarsEnum.OUT_DIR.value, "/data")
        ctx.env.setdefault(VarsEnum.WEB_PORT.value, "8081")

        # The upstream deploy script uploads a filtered subset of the runtime .env to Key Vault.
        # camera-storage-viewer needs FTP_* keys at runtime for the FTP container group.
        # If the user didn't override the default, extend it.
        if hasattr(ctx.args, "upload_env_prefixes"):
            current = str(getattr(ctx.args, "upload_env_prefixes") or "").strip()
            if current == "BASIC_AUTH_":
                setattr(ctx.args, "upload_env_prefixes", "BASIC_AUTH_,FTP_")

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

        # Camera-friendly default for PASV behind ACI NAT:
        # if FTP_PUBLIC_HOST isn't set, set it to the public FTP container group's FQDN.
        # Users can override by setting FTP_PUBLIC_HOST explicitly in runtime .env.
        if not _get_env_str(ctx, VarsEnum.FTP_PUBLIC_HOST.value):
            dns_label = (plan.dns_label or "").strip()
            location = (plan.location or "").strip()
            if dns_label and location:
                ftp_dns_label = f"{dns_label}-ftp"
                ctx.env[VarsEnum.FTP_PUBLIC_HOST.value] = f"{ftp_dns_label}.{location}.azurecontainer.io"

    def post_deploy(self, ctx: DeployContext, plan: DeployPlan, _res: object) -> None:
        """Deploy the FTP container group when docker-compose.yml defines it.

        We intentionally keep this viewer-specific behavior out of the upstream-controlled
        `azure_deploy_container.py` script.
        """

        repo_root: Path = ctx.repo_root
        compose_config = compose_helpers.load_docker_compose_config(repo_root)
        role_map = compose_helpers.detect_services_by_role(compose_config)
        ftp_services = role_map.get("ftp", [])
        if not ftp_services:
            return
        if len(ftp_services) > 1:
            raise SystemExit(f"Multiple ftp services found via x-deploy-role: {ftp_services}")

        ftp_service_name = ftp_services[0]
        services = compose_config.get("services", {})
        ftp_service = services.get(ftp_service_name)
        if not ftp_service:
            raise SystemExit(f"x-deploy-role ftp points to missing service: {ftp_service_name}")

        rg = (_get_arg(ctx.args, "resource_group") or _get_env_str(ctx, "AZURE_RESOURCE_GROUP") or "").strip()
        if not rg:
            raise SystemExit("Missing resource group; cannot deploy ftp container group")

        name = (plan.name or "").strip()
        if not name:
            raise SystemExit("Missing container name in deploy plan")

        storage_name = (_get_arg(ctx.args, "storage_name") or f"{rg}stg").replace("-", "")
        storage_name = "".join([c for c in storage_name.lower() if c.isalnum()])[:24]

        identity_name = _get_arg(ctx.args, "identity_name") or f"{rg}-identity"
        kv_name = _get_arg(ctx.args, "keyvault_name")
        if not kv_name:
            base = "".join([c for c in rg.lower() if c.isalnum()])
            kv_name = f"{base}kv"[:24]

        share_workspace = _get_arg(ctx.args, "share_workspace") or f"{name}-workspace"
        data_share_name = (_get_arg(ctx.args, "data_share_name") or share_workspace).strip()

        # Ports from compose env (preferred) -> runtime env fallback.
        def _compose_int(key: str) -> int | None:
            raw = compose_helpers.get_env_var(ftp_service, key)
            if raw is None:
                return None
            s = str(raw).strip()
            return int(s) if s else None

        ftp_port = _compose_int("FTP_PORT") or _get_env_int(ctx, VarsEnum.FTP_PORT.value, default=21) or 21
        passive_min = (
            _compose_int("FTP_PASSIVE_PORT_MIN")
            or _get_env_int(ctx, VarsEnum.FTP_PASSIVE_PORT_MIN.value, default=50000)
            or 50000
        )
        passive_max = (
            _compose_int("FTP_PASSIVE_PORT_MAX")
            or _get_env_int(ctx, VarsEnum.FTP_PASSIVE_PORT_MAX.value, default=50003)
            or 50003
        )
        _aci_port_limit_validate(ftp_port=ftp_port, passive_min=passive_min, passive_max=passive_max)

        # Registry creds: required for private images, optional for public ones.
        registry_username = _get_env_str(ctx, "GHCR_USERNAME")
        registry_password = _get_env_str(ctx, "GHCR_TOKEN")
        wants_registry_creds = bool(str(ctx.env.get("GHCR_PRIVATE") or "").strip().lower() in {"1", "true", "yes"})

        registry_server = None
        if str(plan.app_image or "").startswith("ghcr.io/") and registry_username and registry_password:
            registry_server = "ghcr.io"

        if wants_registry_creds and not (registry_username and registry_password):
            raise SystemExit("GHCR_PRIVATE=true but GHCR_USERNAME/GHCR_TOKEN are missing (needed to deploy ftp group)")

        # Identity details
        identity_id = str(
            run_az_command(
                ["identity", "show", "-g", rg, "-n", identity_name, "--query", "id", "-o", "tsv"],
                capture_output=True,
            )
        ).strip()
        identity_client_id = str(
            run_az_command(
                ["identity", "show", "-g", rg, "-n", identity_name, "--query", "clientId", "-o", "tsv"],
                capture_output=True,
            )
        ).strip()
        identity_tenant_id = str(
            run_az_command(
                ["identity", "show", "-g", rg, "-n", identity_name, "--query", "tenantId", "-o", "tsv"],
                capture_output=True,
            )
        ).strip()

        # Storage key
        storage_key = str(
            run_az_command(
                [
                    "storage",
                    "account",
                    "keys",
                    "list",
                    "--resource-group",
                    rg,
                    "--account-name",
                    storage_name,
                    "--query",
                    "[0].value",
                    "-o",
                    "tsv",
                ],
                capture_output=True,
            )
        ).strip()

        ftp_group_name = f"{name}-ftp"
        ftp_dns_label = f"{plan.dns_label}-ftp"

        print(
            f"ℹ️  [deploy] docker-compose defines ftp='{ftp_service_name}'. Ensuring ACI container group '{ftp_group_name}' ({ftp_port}, {passive_min}-{passive_max})"
        )

        _delete_container_group(rg=rg, name=ftp_group_name)

        ftp_yaml = ftp_yaml_helpers.generate_deploy_yaml(
            name=ftp_group_name,
            location=plan.location,
            image=plan.app_image,
            registry_server=registry_server,
            registry_username=registry_username,
            registry_password=registry_password,
            identity_id=identity_id,
            identity_client_id=identity_client_id,
            identity_tenant_id=identity_tenant_id,
            storage_name=storage_name,
            storage_key=storage_key,
            kv_name=kv_name,
            dns_label=ftp_dns_label,
            cpu_cores=plan.app_cpu,
            memory_gb=plan.app_memory,
            data_share_name=data_share_name,
            ftp_port=ftp_port,
            ftp_passive_port_min=passive_min,
            ftp_passive_port_max=passive_max,
        )

        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(ftp_yaml)
            ftp_yaml_path = f.name

        print(f"📝 [deploy] wrote: {ftp_yaml_path}")
        run_az_command(["container", "create", "--resource-group", rg, "--file", ftp_yaml_path], capture_output=False)


def get_hooks() -> _ViewerHooks:
    return _ViewerHooks()
