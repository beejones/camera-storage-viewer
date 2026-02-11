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

FTP deployment:
The upstream engine deploys a single container group (web + caddy sidecar).
This hook creates a second ACI container group for FTP after the web group
is deployed, using the `post_deploy` hook. This matches the old
csv_deploy_container.py `--service full` behaviour.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
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


def _int_env(env: dict, name: str, default: int) -> int:
    raw = str(env.get(name, "") or "").strip()
    if not raw:
        return default
    return int(raw)


def _float_env(env: dict, name: str, default: float) -> float:
    raw = str(env.get(name, "") or "").strip()
    if not raw:
        return default
    return float(raw)


class _HooksProto(Protocol):
    def pre_validate_env(self, ctx: Any) -> None: ...

    def post_validate_env(self, ctx: Any) -> None: ...

    def build_deploy_plan(self, ctx: Any, plan: Any) -> None: ...

    def pre_render_yaml(self, ctx: Any, plan: Any) -> None: ...

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

        ftp_port = _int_env(env, "FTP_PORT", 21)
        passive_min = _int_env(env, "FTP_PASSIVE_PORT_MIN", 50000)
        passive_max = _int_env(env, "FTP_PASSIVE_PORT_MAX", 50003)
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

    def pre_render_yaml(self, _ctx: Any, _plan: Any) -> None:
        """Capture infrastructure parameters into plan.extra_metadata.

        The upstream engine resolves these from args + env vars into local
        variables that aren't exposed on DeployPlan. We re-resolve them here
        so post_deploy can use them to create the FTP container group.
        """
        ctx = _ctx
        plan = _plan
        env = _env_view(ctx)
        args = _args_view(ctx)

        rg = str(getattr(args, "resource_group", "") or env.get("AZURE_RESOURCE_GROUP", "") or "").strip()
        name = str(
            getattr(args, "container_name", "") or env.get("AZURE_CONTAINER_NAME", "") or "protected-azure-container"
        ).strip()
        dns_label = str(getattr(args, "dns_label", "") or name).strip().lower()
        location = str(
            getattr(args, "location", "") or env.get("AZURE_LOCATION", "") or "westeurope"
        ).strip()

        storage_name = str(getattr(args, "storage_name", "") or f"{rg}stg").replace("-", "")
        storage_name = "".join([c for c in storage_name.lower() if c.isalnum()])[:24]

        identity_name = str(getattr(args, "identity_name", "") or f"{rg}-identity").strip()

        if getattr(args, "keyvault_name", None):
            kv_name = str(args.keyvault_name).strip()
        else:
            base = "".join([c for c in rg.lower() if c.isalnum()])
            kv_name = f"{base}kv"[:24]

        data_share_name = str(
            getattr(args, "data_share_name", "") or env.get("AZURE_DATA_SHARE_NAME", "") or f"{name}-data"
        ).strip()

        md = getattr(plan, "extra_metadata", {})
        md["_ftp_infra"] = {
            "rg": rg,
            "location": location,
            "name": name,
            "dns_label": dns_label,
            "storage_name": storage_name,
            "identity_name": identity_name,
            "kv_name": kv_name,
            "data_share_name": data_share_name,
        }
        return None

    def post_deploy(self, _ctx: Any, _plan: Any, _result: Any) -> None:
        """Deploy FTP as a separate ACI container group after web+caddy.

        This replicates the old csv_deploy_container.py `--service full`
        behaviour. FTP needs its own container group because ACI limits
        public ports to 5 per group, and web+caddy already uses 80/443.
        """
        ctx = _ctx
        plan = _plan
        env = _env_view(ctx)

        # Only deploy FTP when deploy_mode is "full" (matching old --service full)
        deploy_mode = str(getattr(plan, "deploy_mode", "") or "").strip().lower()
        if deploy_mode != "full":
            return None

        infra = getattr(plan, "extra_metadata", {}).get("_ftp_infra")
        if not infra:
            print("⚠️  [hook] FTP deploy skipped: no infrastructure metadata captured", file=sys.stderr)
            return None

        rg = infra["rg"]
        location = infra["location"]
        name = infra["name"]
        dns_label = infra["dns_label"]
        storage_name = infra["storage_name"]
        identity_name = infra["identity_name"]
        kv_name = infra["kv_name"]
        data_share_name = infra["data_share_name"]

        ftp_name = f"{name}-ftp"
        ftp_dns_label = f"{dns_label}-ftp"

        ftp_port = _int_env(env, "FTP_PORT", 21)
        ftp_passive_port_min = _int_env(env, "FTP_PASSIVE_PORT_MIN", 50000)
        ftp_passive_port_max = _int_env(env, "FTP_PASSIVE_PORT_MAX", 50003)
        ftp_cpu_cores = _float_env(env, "FTP_CPU_CORES", float(getattr(plan, "app_cpu", 1.0)))
        ftp_memory_gb = _float_env(env, "FTP_MEMORY_GB", float(getattr(plan, "app_memory", 2.0)))

        # Get infrastructure details
        try:
            storage_key = str(
                _run_az(
                    [
                        "storage", "account", "keys", "list",
                        "--account-name", storage_name,
                        "--resource-group", rg,
                        "--query", "[0].value",
                        "-o", "tsv",
                    ],
                    capture_output=True,
                ) or ""
            ).strip()

            identity_result = _run_az(
                [
                    "identity", "show",
                    "--name", identity_name,
                    "--resource-group", rg,
                    "--query", "{id: id, clientId: clientId, tenantId: tenantId}",
                    "-o", "json",
                ],
                capture_output=True,
            )
            import json
            identity_info = json.loads(str(identity_result))
            identity_id = identity_info["id"]
            identity_client_id = identity_info.get("clientId")
            identity_tenant_id = identity_info.get("tenantId")
        except Exception as e:
            print(f"❌ [hook] FTP deploy failed: could not resolve infrastructure: {e}", file=sys.stderr)
            raise

        # Get registry credentials from plan metadata
        registry_server = str(env.get("GHCR_PRIVATE", "") or "").strip().lower() in ("1", "true", "yes")
        registry_username = None
        registry_password = None
        if registry_server:
            registry_server_str = "ghcr.io"
            registry_username = str(env.get("GHCR_USERNAME", "") or "").strip() or None
            registry_password = str(env.get("GHCR_TOKEN", "") or "").strip() or None
        else:
            registry_server_str = None

        # Generate FTP ACI YAML using the existing helper
        try:
            import csv_deploy_yaml_helpers as yaml_helpers  # type: ignore
        except ImportError:
            from scripts.deploy import csv_deploy_yaml_helpers as yaml_helpers  # type: ignore

        image = str(getattr(plan, "app_image", "")).strip()
        if not image:
            print("❌ [hook] FTP deploy skipped: no app_image on plan", file=sys.stderr)
            return None

        print(f"\n🚀 [hook] Deploying FTP container group: {ftp_name}")
        print(f"  Image: {image}")
        print(f"  FTP port: {ftp_port}, passive: {ftp_passive_port_min}-{ftp_passive_port_max}")

        try:
            yaml_text = yaml_helpers.generate_deploy_yaml(
                name=ftp_name,
                location=location,
                image=image,
                registry_server=registry_server_str,
                registry_username=registry_username,
                registry_password=registry_password,
                identity_id=identity_id,
                identity_client_id=identity_client_id,
                identity_tenant_id=identity_tenant_id,
                storage_name=storage_name,
                storage_key=storage_key,
                kv_name=kv_name,
                dns_label=ftp_dns_label,
                cpu_cores=ftp_cpu_cores,
                memory_gb=ftp_memory_gb,
                data_share_name=data_share_name,
                ftp_port=ftp_port,
                ftp_passive_port_min=ftp_passive_port_min,
                ftp_passive_port_max=ftp_passive_port_max,
            )
        except ValueError as e:
            raise SystemExit(f"[hook] Invalid FTP ACI configuration: {e}")

        # Delete existing FTP container group if present
        try:
            _run_az(
                ["container", "delete", "--resource-group", rg, "--name", ftp_name, "--yes"],
                capture_output=False,
                ignore_errors=True,
            )
            _wait_for_container_deleted(rg=rg, container_name=ftp_name)
        except Exception:
            pass  # Best-effort cleanup

        # Write YAML and deploy
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(yaml_text)
            yaml_path = f.name

        print(f"📝 [hook] FTP YAML written to: {yaml_path}")

        max_retries = 5
        base_delay = 10.0
        for attempt in range(1, max_retries + 1):
            try:
                _run_az(
                    ["container", "create", "--resource-group", rg, "--file", yaml_path],
                    capture_output=False,
                )
                break
            except subprocess.CalledProcessError as e:
                err = getattr(e, "stderr", "") or ""
                is_transient = "RegistryErrorResponse" in err or "Conflict" in err
                if is_transient and attempt < max_retries:
                    sleep_time = min(60.0, base_delay * (2 ** (attempt - 1)))
                    print(
                        f"⚠️  [hook] FTP registry/ACI transient error (attempt {attempt}/{max_retries}). "
                        f"Retrying in {sleep_time:.0f}s..."
                    )
                    time.sleep(sleep_time)
                else:
                    raise

        print(f"\n✅ [hook] FTP deployed.")
        print(f"  FTP FQDN: {ftp_dns_label}.{location}.azurecontainer.io")
        print(f"  FTP URL:  ftp://{ftp_dns_label}.{location}.azurecontainer.io:{ftp_port}")
        return None


def _run_az(args: list[str], *, capture_output: bool = False, ignore_errors: bool = False) -> str | None:
    """Run an `az` CLI command."""
    cmd = ["az"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture_output,
            text=True,
            check=not ignore_errors,
        )
        if capture_output:
            return result.stdout
        return None
    except subprocess.CalledProcessError:
        if ignore_errors:
            return None
        raise


def _wait_for_container_deleted(*, rg: str, container_name: str, max_wait: int = 120) -> None:
    """Wait for an ACI container group to be fully deleted."""
    print(f"⏳ [hook] Waiting for FTP container '{container_name}' deletion...")
    poll_interval = 5
    waited = 0
    while waited < max_wait:
        result = _run_az(
            [
                "container", "show",
                "--resource-group", rg,
                "--name", container_name,
                "--query", "provisioningState",
                "-o", "tsv",
            ],
            capture_output=True,
            ignore_errors=True,
        )
        if result is None or not result.strip():
            print(f"✅ [hook] FTP container '{container_name}' deleted after {waited}s")
            return
        state = result.strip().lower()
        if state in ("deleting", "pending"):
            print(f"⏳ [hook] FTP container '{container_name}' still {state}...")
        time.sleep(poll_interval)
        waited += poll_interval
    print(f"⚠️  [hook] Timed out waiting for '{container_name}' deletion after {max_wait}s, proceeding anyway...")


_HOOKS: _HooksProto = CameraStorageViewerHooks()


def get_hooks() -> _HooksProto:
    return _HOOKS
