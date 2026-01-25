#!/usr/bin/env python3
"""Deploy camera-storage-viewer to Azure Container Instances (ACI).

Current model (FTP-only):
- Single container group with an FTP server.
- Runtime .env is stored as a Key Vault secret (default: 'env') and fetched at startup via Managed Identity.
- A single Azure Files share is mounted at /data for durable storage.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv


# Add scripts dir to path to allow importing sibling modules when running as a script.
sys.path.append(str(Path(__file__).parent))

import azure_deploy_container_helpers as deploy_helpers
import azure_deploy_yaml_helpers as yaml_helpers

from env_schema import (
    DEPLOY_SCHEMA,
    RUNTIME_SCHEMA,
    EnvTarget,
    EnvValidationError,
    SecretsEnum,
    VarsEnum,
    apply_defaults,
    get_spec,
    parse_dotenv_file,
    validate_cross_field_rules,
    validate_known_keys,
    validate_required,
    write_dotenv_values,
)

try:
    from azure_utils import kv_data_plane_available, kv_secret_set_quiet, run_az_command
except ImportError:
    sys.path.append("scripts")
    from azure_utils import kv_data_plane_available, kv_secret_set_quiet, run_az_command


DEFAULT_CPU_CORES = float(get_spec(DEPLOY_SCHEMA, VarsEnum.DEFAULT_CPU_CORES).default or "1.0")
DEFAULT_MEMORY_GB = float(get_spec(DEPLOY_SCHEMA, VarsEnum.DEFAULT_MEMORY_GB).default or "2.0")


# Keep helper wiring centralized here; `main()` continues to use the historic names.
materialize_deploy_env_file_if_missing = deploy_helpers.materialize_deploy_env_file_if_missing
ensure_oidc_app_and_sp = deploy_helpers.ensure_oidc_app_and_sp
sync_github_actions_vars_secrets = deploy_helpers.sync_github_actions_vars_secrets

ensure_infra = deploy_helpers.ensure_infra
ensure_oidc_app_role_assignment = deploy_helpers.ensure_oidc_app_role_assignment

docker_pull = deploy_helpers.docker_pull
docker_login = deploy_helpers.docker_login
docker_build = deploy_helpers.docker_build
docker_push = deploy_helpers.docker_push

parse_image_ref = deploy_helpers.parse_image_ref
ghcr_repo_prefix_for_image = deploy_helpers.ghcr_repo_prefix_for_image

is_interactive = deploy_helpers.is_interactive
az_logged_in = deploy_helpers.az_logged_in

kv_secret_get = deploy_helpers.kv_secret_get
kv_secret_set = deploy_helpers.kv_secret_set

prompt_value = deploy_helpers.prompt_value
prompt_secret = deploy_helpers.prompt_secret
prompt_yes_no = deploy_helpers.prompt_yes_no

truthy = deploy_helpers.truthy
looks_like_bcrypt_hash = deploy_helpers.looks_like_bcrypt_hash
bcrypt_hash_password = deploy_helpers.bcrypt_hash_password
resolve_value = deploy_helpers.resolve_value

get_storage_key = deploy_helpers.get_storage_key
get_identity_details = deploy_helpers.get_identity_details
ensure_file_share_exists = deploy_helpers.ensure_file_share_exists

_env_filtered_content = deploy_helpers._env_filtered_content
_format_keyvault_set_help = deploy_helpers._format_keyvault_set_help
_hint_for_ghcr_scope_error = deploy_helpers._hint_for_ghcr_scope_error


def generate_deploy_yaml(
    *,
    name: str,
    location: str,
    image: str,
    registry_server: str | None,
    registry_username: str | None,
    registry_password: str | None,
    identity_id: str,
    identity_client_id: str | None,
    identity_tenant_id: str | None,
    storage_name: str,
    storage_key: str,
    kv_name: str,
    dns_label: str,
    cpu_cores: float,
    memory_gb: float,
    data_share_name: str,
    ftp_port: int = 21,
    ftp_passive_port_min: int = 50000,
    ftp_passive_port_max: int = 50003,
) -> str:
    """Back-compat re-export for tests and external callers."""

    return yaml_helpers.generate_deploy_yaml(
        name=name,
        location=location,
        image=image,
        registry_server=registry_server,
        registry_username=registry_username,
        registry_password=registry_password,
        identity_id=identity_id,
        identity_client_id=identity_client_id,
        identity_tenant_id=identity_tenant_id,
        storage_name=storage_name,
        storage_key=storage_key,
        kv_name=kv_name,
        dns_label=dns_label,
        cpu_cores=cpu_cores,
        memory_gb=memory_gb,
        data_share_name=data_share_name,
        ftp_port=ftp_port,
        ftp_passive_port_min=ftp_passive_port_min,
        ftp_passive_port_max=ftp_passive_port_max,
    )


def generate_deploy_yaml_web(
    *,
    name: str,
    location: str,
    image: str,
    registry_server: str | None,
    registry_username: str | None,
    registry_password: str | None,
    identity_id: str,
    identity_client_id: str | None,
    identity_tenant_id: str | None,
    storage_name: str,
    storage_key: str,
    kv_name: str,
    dns_label: str,
    cpu_cores: float,
    memory_gb: float,
    data_share_name: str,
    web_port: int = 80,
) -> str:
    """Generate ACI YAML for the viewer web service (web-only container group)."""

    return yaml_helpers.generate_deploy_yaml_web(
        name=name,
        location=location,
        image=image,
        registry_server=registry_server,
        registry_username=registry_username,
        registry_password=registry_password,
        identity_id=identity_id,
        identity_client_id=identity_client_id,
        identity_tenant_id=identity_tenant_id,
        storage_name=storage_name,
        storage_key=storage_key,
        kv_name=kv_name,
        dns_label=dns_label,
        cpu_cores=cpu_cores,
        memory_gb=memory_gb,
        data_share_name=data_share_name,
        web_port=web_port,
    )


def generate_deploy_yaml_web_caddy(
    *,
    name: str,
    location: str,
    image: str,
    registry_server: str | None,
    registry_username: str | None,
    registry_password: str | None,
    identity_id: str,
    identity_client_id: str | None,
    identity_tenant_id: str | None,
    storage_name: str,
    storage_key: str,
    kv_name: str,
    dns_label: str,
    cpu_cores: float,
    memory_gb: float,
    data_share_name: str,
    public_domain: str,
    acme_email: str | None = None,
    caddy_image: str = "caddy:2",
    web_port: int = 8081,
) -> str:
    """Generate ACI YAML for the viewer web service behind Caddy (80/443)."""

    return yaml_helpers.generate_deploy_yaml_web_caddy(
        name=name,
        location=location,
        image=image,
        registry_server=registry_server,
        registry_username=registry_username,
        registry_password=registry_password,
        identity_id=identity_id,
        identity_client_id=identity_client_id,
        identity_tenant_id=identity_tenant_id,
        storage_name=storage_name,
        storage_key=storage_key,
        kv_name=kv_name,
        dns_label=dns_label,
        cpu_cores=cpu_cores,
        memory_gb=memory_gb,
        data_share_name=data_share_name,
        public_domain=public_domain,
        acme_email=acme_email,
        caddy_image=caddy_image,
        web_port=web_port,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy camera-storage-viewer to Azure Container Instances")

    parser.add_argument(
        "--service",
        choices=["ftp", "web", "web-caddy"],
        default="ftp",
        help=(
            "Which service to deploy. 'ftp' deploys the FTP-only container group (default). "
            "'web' deploys a web-only container group that exposes a single HTTP port (typically 80). "
            "'web-caddy' deploys web plus a Caddy sidecar exposing ports 80/443 for a custom domain."
        ),
    )

    # These can come from --env-file (recommended) so they are not required.
    parser.add_argument("--resource-group", "-g", required=False, default=None)
    parser.add_argument("--location", "-l", default=None)
    parser.add_argument("--container-name", "-n", default=None)
    parser.add_argument("--dns-label", default=None, help="DNS label for <label>.<location>.azurecontainer.io")

    parser.add_argument("--image", "-i", default=None, help="Container image URL")

    parser.add_argument("--storage-name", default=None)
    parser.add_argument("--identity-name", default=None)
    parser.add_argument("--keyvault-name", default=None)

    parser.add_argument(
        "--data-share-name",
        default=None,
        help="Azure Files share name to mount at /data (default: <container>-data)",
    )
    # Back-compat alias from the older code-server-based deploy model.
    parser.add_argument("--share-workspace", dest="data_share_name", default=None, help=argparse.SUPPRESS)

    parser.add_argument(
        "--build",
        action="store_true",
        help="Build the container image locally before deploy (docker build)",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Push the container image before deploy (docker push)",
    )
    parser.add_argument(
        "--build-push",
        action="store_true",
        help="Build and push the container image before deploy",
    )

    parser.add_argument(
        "--publish",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Build + push the image before deploy (default: enabled when GHCR_PRIVATE=true)",
    )
    parser.add_argument(
        "--docker-context",
        default=None,
        help="Docker build context directory (default: repo root)",
    )
    parser.add_argument(
        "--dockerfile",
        default=None,
        help="Optional Dockerfile path (default: use Docker's default resolution)",
    )

    parser.add_argument("--interactive", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument(
        "--persist-to-keyvault",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Offer to save entered deploy-time values to Key Vault secrets (default: off)",
    )

    parser.add_argument("--image-secret", default="image")
    parser.add_argument(
        "--env-file",
        default=None,
        help=(
            "Env file to load for deploy-time values (default: repo root .env.deploy). "
            "Note: deployment always loads .env first, then this deploy env file on top (deploy-time overrides)."
        ),
    )

    parser.add_argument(
        "--azure-oidc-app-name",
        default=None,
        help=(
            "Azure AD App Registration name for GitHub Actions OIDC (required; set AZURE_OIDC_APP_NAME in .env.deploy "
            "or pass --azure-oidc-app-name)"
        ),
    )

    parser.add_argument(
        "--set-vars-secrets",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Sync deploy-time vars/secrets to GitHub Actions (via scripts/deploy/gh_sync_actions_env.py). "
            "Default: enabled. Use --no-set-vars-secrets in CI."
        ),
    )

    parser.add_argument(
        "--nuke-github-secrets",
        action="store_true",
        help="Run scripts/deploy/gh_nuke_secrets.py to delete ALL GitHub Actions secrets/vars before deploying.",
    )

    parser.add_argument(
        "--validate-dotenv",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Validate required keys and cross-field rules from .env/.env.deploy before deploying (default: enabled). "
            "Use --no-validate-dotenv to allow interactive prompting for missing values."
        ),
    )

    parser.add_argument(
        "--write-back-deploy-env",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write derived AZURE_* IDs back into the deploy env file (default: enabled)",
    )

    parser.add_argument(
        "--upload-env",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Upload runtime env to Key Vault secret 'env' before deploy (default: enabled)",
    )
    parser.add_argument(
        "--upload-env-file",
        default=None,
        help="Path to runtime env file to upload (default: repo root .env)",
    )
    parser.add_argument(
        "--upload-env-secret-name",
        default="env",
        help="Key Vault secret name for runtime env (default: env)",
    )
    parser.add_argument(
        "--upload-env-prefixes",
        default="FTP_,OUT_DIR,RETENTION_",
        help="Comma-separated prefixes to include when uploading env (default: FTP_,OUT_DIR,RETENTION_)",
    )
    parser.add_argument(
        "--upload-env-raw",
        action="store_true",
        help="Upload the full env file content (DANGER: may include deploy-only secrets)",
    )

    parser.add_argument(
        "--cpu",
        type=float,
        default=None,
        help=f"CPU cores (default: {VarsEnum.DEFAULT_CPU_CORES.value} from .env.deploy, fallback {DEFAULT_CPU_CORES})",
    )
    parser.add_argument(
        "--memory",
        type=float,
        default=None,
        help=f"Memory GB (default: {VarsEnum.DEFAULT_MEMORY_GB.value} from .env.deploy, fallback {DEFAULT_MEMORY_GB})",
    )

    args = parser.parse_args()

    if not az_logged_in():
        raise SystemExit("Not logged into Azure. Run: az login")

    # Allow --azure-oidc-app-name to satisfy schema validation by surfacing it as an env var.
    if args.azure_oidc_app_name and str(args.azure_oidc_app_name).strip():
        os.environ[VarsEnum.AZURE_OIDC_APP_NAME.value] = str(args.azure_oidc_app_name).strip()

    # scripts/deploy/azure_deploy_container.py -> repo root is 2 parents up.
    repo_root = Path(__file__).resolve().parents[2]

    interactive = is_interactive() if args.interactive is None else bool(args.interactive)
    # Key Vault is used at *runtime* by the container to fetch the full .env secret.
    # For deploy-time inputs (image/domain/registry creds), default to local env/args.
    persist_to_kv = bool(args.persist_to_keyvault)

    # Load deploy-time values.
    # We load .env (runtime config) first, then .env.deploy (deploy-time overrides) on top.
    runtime_env_path = repo_root / ".env"

    deploy_env_path: Path = (
        Path(args.env_file).expanduser().resolve() if args.env_file else (repo_root / ".env.deploy")
    )

    # Strict validation of provided dotenv files (if present).
    try:
        if runtime_env_path.exists():
            runtime_kv = parse_dotenv_file(runtime_env_path)
            validate_known_keys(RUNTIME_SCHEMA, runtime_kv, context=f"runtime ({runtime_env_path.name})")

        if deploy_env_path.exists():
            deploy_kv_file = parse_dotenv_file(deploy_env_path)
            validate_known_keys(DEPLOY_SCHEMA, deploy_kv_file, context=f"deploy ({deploy_env_path.name})")
    except EnvValidationError as e:
        print(e.format(), file=sys.stderr)
        raise SystemExit(2)

    # load_dotenv() is a no-op if the file doesn't exist.
    # override=True ensures deploy-time vars take precedence over runtime vars.
    if runtime_env_path.exists():
        load_dotenv(dotenv_path=str(runtime_env_path), override=False)
    if deploy_env_path.exists():
        load_dotenv(dotenv_path=str(deploy_env_path), override=True)
    elif not runtime_env_path.exists():
        # If neither exists, materialize .env.deploy if we have env vars (CI case)
        materialize_deploy_env_file_if_missing(path=deploy_env_path)

    # Treat AZURE_OIDC_APP_NAME as deploy-script-derived when missing.
    # We keep it mandatory in the schema for determinism, but avoid forcing users
    # to hand-edit generated .env.deploy files.
    if not (os.getenv(VarsEnum.AZURE_OIDC_APP_NAME.value) or (args.azure_oidc_app_name or "").strip()):
        os.environ[VarsEnum.AZURE_OIDC_APP_NAME.value] = f"{repo_root.name}-github-actions-oidc"

    # Validate up-front so we fail fast with a full list of missing/invalid keys.
    # This avoids later partial failures like "Missing resource group".
    if bool(args.validate_dotenv):
        try:
            if runtime_env_path.exists():
                runtime_kv = parse_dotenv_file(runtime_env_path)
                runtime_kv = apply_defaults(RUNTIME_SCHEMA, runtime_kv)
                validate_required(RUNTIME_SCHEMA, runtime_kv, context=f"runtime ({runtime_env_path.name})")

            deploy_kv_file = parse_dotenv_file(deploy_env_path) if deploy_env_path.exists() else {}
            deploy_schema_keys = {spec.key.value for spec in DEPLOY_SCHEMA}
            deploy_kv_env = {k: v for k, v in os.environ.items() if k in deploy_schema_keys and str(v).strip()}
            deploy_kv = dict(deploy_kv_file)
            deploy_kv.update(deploy_kv_env)
            deploy_kv = apply_defaults(DEPLOY_SCHEMA, deploy_kv)

            if not deploy_kv.get(VarsEnum.AZURE_DNS_LABEL.value):
                deploy_kv[VarsEnum.AZURE_DNS_LABEL.value] = deploy_kv.get(VarsEnum.AZURE_CONTAINER_NAME.value, "").strip()

            deploy_dotenv_specs = [spec for spec in DEPLOY_SCHEMA if EnvTarget.DOTENV_DEPLOY in spec.targets]
            validate_required(deploy_dotenv_specs, deploy_kv, context=f"deploy ({deploy_env_path.name} + env)")
            validate_cross_field_rules(deploy_kv=deploy_kv, context=f"deploy ({deploy_env_path.name} + env)")
        except EnvValidationError as e:
            print(e.format(), file=sys.stderr)
            raise SystemExit(2)

    # (Non-interactive enforcement is handled by --validate-dotenv, enabled by default.)



    # Resolve Azure OIDC App (create if missing) so sync script has correct ID.
    # Prioritize: Env EnvVar -> Arg Default -> Lookup/Create
    oidc_client_id = (os.getenv(VarsEnum.AZURE_CLIENT_ID.value) or "").strip()
    if not oidc_client_id and bool(args.set_vars_secrets):
        oidc_app_name = (
            (args.azure_oidc_app_name or "").strip()
            or (os.getenv(VarsEnum.AZURE_OIDC_APP_NAME.value) or "").strip()
        )
        if not oidc_app_name:
            if interactive and not bool(args.validate_dotenv):
                oidc_app_name = prompt_value("Azure OIDC App Registration name")
            if not oidc_app_name:
                raise SystemExit(
                    "Missing Azure OIDC app name. Set AZURE_OIDC_APP_NAME in .env.deploy (recommended) "
                    "or pass --azure-oidc-app-name."
                )
        oidc_client_id = ensure_oidc_app_and_sp(display_name=oidc_app_name)
        # Set in env so downstream logic can use it
        os.environ[VarsEnum.AZURE_CLIENT_ID.value] = oidc_client_id

    # Keep GitHub Actions vars/secrets in sync by default (so CI has what it needs).
    # In CI, pass --no-set-vars-secrets.
    if bool(args.nuke_github_secrets):
        nuke_script = repo_root / "scripts" / "deploy" / "gh_nuke_secrets.py"
        if nuke_script.exists():
            print(f"🧨 [deploy] Nuking GitHub secrets: python3 {nuke_script}")
            # We pass --yes if the deploy script is not interactive? 
            # Actually, gh_nuke_secrets.py is interactive by default. 
            # If the user passed --no-interactive to this script, we should probably pass --yes to nuke.
            nuke_cmd = [sys.executable, str(nuke_script)]
            if not interactive:
                 nuke_cmd.append("--yes")
            
            subprocess.run(nuke_cmd, check=True)
        else:
            print(f"⚠️  [deploy] Nuke script not found: {nuke_script}", file=sys.stderr)

    if bool(args.set_vars_secrets):
        sync_github_actions_vars_secrets(repo_root=repo_root, deploy_env_path=deploy_env_path, azure_client_id=oidc_client_id)

    rg = (args.resource_group or os.getenv(VarsEnum.AZURE_RESOURCE_GROUP.value) or "").strip()
    if not rg:
        raise SystemExit(
            "Missing resource group. Provide --resource-group, or set AZURE_RESOURCE_GROUP in .env.deploy (or pass --env-file)."
        )

    location = (args.location or os.getenv(VarsEnum.AZURE_LOCATION.value) or "westeurope").strip() or "westeurope"
    name = (
        args.container_name
        or os.getenv(VarsEnum.AZURE_CONTAINER_NAME.value)
        or "camera-storage-viewer"
    ).strip() or "camera-storage-viewer"
    dns_label = (args.dns_label or name).strip().lower()

    storage_name = (args.storage_name or f"{rg}stg").replace("-", "")
    storage_name = "".join([c for c in storage_name.lower() if c.isalnum()])[:24]

    # Sanitize Key Vault name: <24 chars, alphanumeric/hyphens, no start/end hyphen.
    # Default: derived from RG name.
    # We strip hyphens to save space and reduce risk of consecutive hyphens.
    identity_name = args.identity_name or f"{rg}-identity"

    if args.keyvault_name:
        kv_name = args.keyvault_name
    else:
        # e.g. "camera-storage-viewer-rg" -> "protectedazurecontainkv"
        base = "".join([c for c in rg.lower() if c.isalnum()])
        kv_name = f"{base}kv"[:24]

    # Ensure Azure resources exist so a single azure_deploy_container invocation can bootstrap infra.
    # FTP-only: single durable data share mounted at /data.
    shares_to_ensure = [args.data_share_name or f"{name}-data"]
    ensure_infra(
        resource_group=rg,
        location=location,
        container_name=name,
        identity_name=identity_name,
        keyvault_name=kv_name,
        storage_name=storage_name,
        shares=shares_to_ensure,
    )

    subscription_id = (os.getenv(VarsEnum.AZURE_SUBSCRIPTION_ID.value) or "").strip()
    if not subscription_id:
        subscription_id = str(
            run_az_command(["account", "show", "--query", "id", "-o", "tsv"], capture_output=True)
        ).strip()

    tenant_id = (os.getenv(VarsEnum.AZURE_TENANT_ID.value) or "").strip()
    if not tenant_id:
        tenant_id = str(
            run_az_command(["account", "show", "--query", "tenantId", "-o", "tsv"], capture_output=True)
        ).strip()

    if subscription_id:
        os.environ[VarsEnum.AZURE_SUBSCRIPTION_ID.value] = subscription_id
    if tenant_id:
        os.environ[VarsEnum.AZURE_TENANT_ID.value] = tenant_id

    if bool(args.write_back_deploy_env):
        updates: dict[str, str] = {}
        if oidc_client_id:
            updates[VarsEnum.AZURE_CLIENT_ID.value] = oidc_client_id
        if tenant_id:
            updates[VarsEnum.AZURE_TENANT_ID.value] = tenant_id
        if subscription_id:
            updates[VarsEnum.AZURE_SUBSCRIPTION_ID.value] = subscription_id
        oidc_app_name_for_writeback = (os.getenv(VarsEnum.AZURE_OIDC_APP_NAME.value) or "").strip()
        if oidc_app_name_for_writeback:
            updates[VarsEnum.AZURE_OIDC_APP_NAME.value] = oidc_app_name_for_writeback
        if updates:
            write_dotenv_values(path=deploy_env_path, updates=updates, create=True)
            print(f"🔑 [env] Updated {deploy_env_path} with derived Azure IDs")
    if oidc_client_id and subscription_id:
        ensure_oidc_app_role_assignment(
            subscription_id=subscription_id, 
            resource_group=rg, 
            client_id=oidc_client_id,
            keyvault_name=kv_name
        )

    # Upload runtime env to Key Vault for the container to fetch at startup.
    # By default we upload only selected runtime keys (see --upload-env-prefixes) to avoid leaking deploy credentials.
    if args.upload_env:
        # By default, always upload the repo root .env (runtime) so KV has the latest
        # runtime configuration, even if deploy-time values are loaded from .env.deploy.
        default_runtime_env = repo_root / ".env"
        upload_env_path = Path(args.upload_env_file).resolve() if args.upload_env_file else default_runtime_env

        # Safety: never upload deploy-only env files to Key Vault.
        if upload_env_path.name in {"env.deploy", ".env.deploy"}:
            raise SystemExit(
                f"Refusing to upload deploy-only env file to Key Vault: {upload_env_path}. "
                "Put runtime settings in .env (repo root) or pass --upload-env-file <runtime_env>."
            )
        if not upload_env_path.exists():
            raise SystemExit(
                f"Runtime env file not found: {upload_env_path}. "
                "Create .env (runtime) or pass --no-upload-env if you want to deploy without uploading."
            )

        prefixes = [p.strip() for p in (args.upload_env_prefixes or "").split(",") if p.strip()]
        try:
            env_content = _env_filtered_content(env_path=upload_env_path, prefixes=prefixes, raw=bool(args.upload_env_raw))
            kv_secret_set_quiet(vault_name=kv_name, secret_name=str(args.upload_env_secret_name), value=env_content)
        except subprocess.CalledProcessError as e:
            print(_format_keyvault_set_help(vault_name=kv_name, stderr=getattr(e, "stderr", None)), file=sys.stderr)
            raise SystemExit(1)

    kv_name_for_secrets = ""
    if persist_to_kv:
        kv_ok = kv_data_plane_available(kv_name)
        if not kv_ok:
            print("[deploy] Disabling --persist-to-keyvault because Key Vault is not reachable.", file=sys.stderr)
            persist_to_kv = False
        else:
            kv_name_for_secrets = kv_name

    data_share_name = shares_to_ensure[0]

    image = resolve_value(
        name="image",
        arg_value=args.image,
        env_names=[VarsEnum.CONTAINER_IMAGE.value],
        kv_name=kv_name_for_secrets,
        kv_secret_name=args.image_secret,
        interactive=interactive,
        secret=False,
        prompt_label="Container image (e.g. ghcr.io/<owner>/camera-storage-viewer:tag)",
        persist_to_kv=persist_to_kv,
    )
    if not image:
        raise SystemExit(
            "Missing container image. Provide --image, set CONTAINER_IMAGE, or store Key Vault secret 'image'."
        )

    # Resolve build/push mode.
    build_requested = bool(args.build or args.build_push)
    push_requested = bool(args.push or args.build_push)

    # FTP-only deployment: no public domain/TLS proxy/basic auth required.

    # Optional registry credentials for private images (e.g. GHCR).
    # If deploying a public image, do not prompt for registry settings.
    ghcr_private = truthy(os.getenv(VarsEnum.GHCR_PRIVATE.value))

    # If the image is private and the user didn't specify any build/push flags,
    # default to publishing the image so a single command works end-to-end.
    if not (args.build or args.push or args.build_push):
        # Default to build-push only for private GHCR images (or when user opts in)
        publish_default = ghcr_private
        publish = publish_default if args.publish is None else bool(args.publish)
        if publish:
            build_requested = True
            push_requested = True

    # GHCR-only: when the image is private (or we are pushing), require GHCR credentials.
    registry_server: str | None = None
    registry_username: str | None = None
    registry_password: str | None = None

    # If credentials are already provided via env, include them in the ACI YAML even
    # when GHCR_PRIVATE is not set. This avoids ACI (InaccessibleImage) surprises.
    has_ghcr_creds_env = bool(
        str(os.getenv(VarsEnum.GHCR_USERNAME.value) or "").strip()
        and str(os.getenv(SecretsEnum.GHCR_TOKEN.value) or "").strip()
    )

    wants_registry_creds = bool(ghcr_private or push_requested or has_ghcr_creds_env)
    if wants_registry_creds:
        registry_server = "ghcr.io"

        # Default username from image owner if it's a ghcr.io/<owner>/... ref.
        registry_username_default = None
        if image.startswith("ghcr.io/"):
            try:
                registry_username_default = image.split("/")[1]
            except IndexError:
                pass

        registry_username = resolve_value(
            name="ghcr_username",
            arg_value=None,
            env_names=[VarsEnum.GHCR_USERNAME.value],
            kv_name=kv_name_for_secrets,
            kv_secret_name=None,
            interactive=interactive,
            secret=False,
            prompt_label="GHCR username",
            persist_to_kv=False,
            default=registry_username_default,
        )

        # Common footgun: env.deploy.example uses a placeholder image.
        # If the user forgot to change it, we'll rewrite it to the resolved username.
        # This avoids GHCR errors like: denied: permission_denied: create_package
        if registry_username:
            placeholder_prefixes = ("ghcr.io/your-user/", "ghcr.io/YOUR-USER/", "ghcr.io/your_user/")
            for prefix in placeholder_prefixes:
                if image.startswith(prefix):
                    image = f"ghcr.io/{registry_username}/" + image[len(prefix) :]
                    print(f"🔧 [docker] Rewrote image to: {image}")
                    break

        registry_password = resolve_value(
            name="ghcr_token",
            arg_value=None,
            env_names=[SecretsEnum.GHCR_TOKEN.value],
            kv_name=kv_name_for_secrets,
            kv_secret_name=None,
            interactive=interactive,
            secret=True,
            prompt_label="GHCR token",
            persist_to_kv=False,
        )

        if ghcr_private and not (registry_username and registry_password):
            raise SystemExit(
                "GHCR_PRIVATE=true but GHCR credentials are incomplete. Set GHCR_USERNAME/GHCR_TOKEN."
            )

    # If we are pushing, ensure we have registry info/creds.
    if push_requested:
        if not registry_server:
            raise SystemExit(
                "Cannot determine registry server for push. For GHCR-only mode, set GHCR_PRIVATE=true and ensure CONTAINER_IMAGE is a ghcr.io/... ref."
            )

        # For pushes, credentials are required even if the image is public.
        if not (registry_username and registry_password):
            raise SystemExit(
                "--push/--build-push requires GHCR credentials. Set GHCR_USERNAME/GHCR_TOKEN."
            )

    # Build/push before deploy if requested.
    if build_requested or push_requested:
        # Docker operations happen from the repo root by default.
        docker_context = (args.docker_context or str(repo_root)).strip() or str(repo_root)
        dockerfile = (args.dockerfile or "").strip() or None

        # Auto-detect our Dockerfile location.
        # Keep default context as repo root so COPY can include files like requirements.txt.
        if not dockerfile:
            candidate = repo_root / "docker" / "Dockerfile"
            if candidate.exists():
                dockerfile = str(candidate)

        # Resolve relative Dockerfile paths against repo root for determinism.
        if dockerfile:
            dockerfile_path = Path(dockerfile)
            if not dockerfile_path.is_absolute():
                dockerfile = str((repo_root / dockerfile_path).resolve())

        if build_requested:
            print(f"🏗️  [docker] building image: {image}")
            try:
                docker_build(image=image, context_dir=docker_context, dockerfile=dockerfile)
            except FileNotFoundError:
                raise SystemExit("Docker not found. Install Docker and ensure 'docker' is on PATH.")

        if push_requested:
            assert registry_server and registry_username and registry_password
            try:
                docker_login(registry=registry_server, username=registry_username, token=registry_password)
                print(f"📦 [docker] pushing image: {image}")
                try:
                    docker_push(image=image)
                except subprocess.CalledProcessError as e:
                    hint = _hint_for_ghcr_scope_error(getattr(e, "stderr", None))
                    if hint:
                        print(hint, file=sys.stderr)
                    raise
            except FileNotFoundError:
                raise SystemExit("Docker not found. Install Docker and ensure 'docker' is on PATH.")

    # Normalize: if no username/password provided, treat registry creds as disabled.
    # This prevents a defaulted registry_server (e.g. ghcr.io) from triggering the
    # "partial credentials" failure for public images.
    if not registry_username and not registry_password:
        registry_server = None

    if any([registry_server, registry_username, registry_password]) and not all([registry_server, registry_username, registry_password]):
        raise SystemExit(
            "Partial registry credentials provided. You must set all of registry server/username/password or none (for public images)."
        )

    storage_key = get_storage_key(storage_name, rg)
    identity_id, identity_client_id, identity_tenant_id = get_identity_details(identity_name, rg)

    # Recreate container group for identity/env updates.
    # Delete existing container if any, then wait for Azure to fully clean up to prevent "Conflict" errors.
    run_az_command(["container", "delete", "--resource-group", rg, "--name", name, "--yes"], capture_output=False, ignore_errors=True)
    
    # Wait for container to be fully deleted (not just deletion initiated)
    print("⏳ [deploy] Waiting for previous container to be fully deleted...")
    max_wait = 120  # seconds
    poll_interval = 5
    waited = 0
    while waited < max_wait:
        # Check if container still exists
        result = run_az_command(
            ["container", "show", "--resource-group", rg, "--name", name, "--query", "provisioningState", "-o", "tsv"],
            capture_output=True,
            ignore_errors=True,
            verbose=False,
        )
        if result is None:
            # Container no longer exists
            print(f"✅ [deploy] Previous container deleted after {waited}s")
            break
        state = str(result).strip().lower()
        if state in ("deleting", "pending"):
            print(f"⏳ [deploy] Container still {state}... waiting")
        time.sleep(poll_interval)
        waited += poll_interval
    else:
        print(f"⚠️  [deploy] Timed out waiting for container deletion after {max_wait}s, proceeding anyway...")

    # FTP-only deployment: no sidecar images to prefetch.

    cpu_cores = float(
        args.cpu
        if args.cpu is not None
        else (os.getenv(VarsEnum.DEFAULT_CPU_CORES.value) or str(DEFAULT_CPU_CORES))
    )
    memory_gb = float(
        args.memory
        if args.memory is not None
        else (os.getenv(VarsEnum.DEFAULT_MEMORY_GB.value) or str(DEFAULT_MEMORY_GB))
    )

    service = str(args.service or "ftp").strip().lower()

    if service == "web":
        # Web-only container group: expose one port (default: 80).
        web_port = int((os.getenv(VarsEnum.WEB_PORT.value) or "80").strip() or "80")
        try:
            yaml_text = generate_deploy_yaml_web(
                name=name,
                location=location,
                image=image,
                registry_server=registry_server,
                registry_username=registry_username,
                registry_password=registry_password,
                identity_id=identity_id,
                identity_client_id=identity_client_id,
                identity_tenant_id=identity_tenant_id,
                storage_name=storage_name,
                storage_key=storage_key,
                kv_name=kv_name,
                dns_label=dns_label,
                cpu_cores=cpu_cores,
                memory_gb=memory_gb,
                data_share_name=data_share_name,
                web_port=web_port,
            )
        except ValueError as e:
            raise SystemExit(f"[deploy] Invalid ACI configuration: {e}")
    elif service == "web-caddy":
        # Web + Caddy sidecar: expose 80/443 only.
        public_domain = str(os.getenv(VarsEnum.PUBLIC_DOMAIN.value) or "").strip()
        if not public_domain:
            raise SystemExit(
                f"[deploy] {VarsEnum.PUBLIC_DOMAIN.value} is required for --service web-caddy (e.g. camera-storage-viewer.zenia.eu)"
            )
        acme_email = str(os.getenv(VarsEnum.ACME_EMAIL.value) or "").strip() or None
        web_port = int((os.getenv(VarsEnum.WEB_PORT.value) or "8081").strip() or "8081")
        try:
            yaml_text = generate_deploy_yaml_web_caddy(
                name=name,
                location=location,
                image=image,
                registry_server=registry_server,
                registry_username=registry_username,
                registry_password=registry_password,
                identity_id=identity_id,
                identity_client_id=identity_client_id,
                identity_tenant_id=identity_tenant_id,
                storage_name=storage_name,
                storage_key=storage_key,
                kv_name=kv_name,
                dns_label=dns_label,
                cpu_cores=cpu_cores,
                memory_gb=memory_gb,
                data_share_name=data_share_name,
                public_domain=public_domain,
                acme_email=acme_email,
                web_port=web_port,
            )
        except ValueError as e:
            raise SystemExit(f"[deploy] Invalid ACI configuration: {e}")
    else:
        ftp_port = int((os.getenv(VarsEnum.FTP_PORT.value) or "21").strip() or "21")
        ftp_passive_port_min = int((os.getenv(VarsEnum.FTP_PASSIVE_PORT_MIN.value) or "50000").strip() or "50000")
        ftp_passive_port_max = int((os.getenv(VarsEnum.FTP_PASSIVE_PORT_MAX.value) or "50003").strip() or "50003")

        # Azure Container Instances has a hard limit of 5 public ports per container group.
        # FTP needs 1 control port + N passive ports.
        ports_unique = sorted(set([ftp_port] + list(range(ftp_passive_port_min, ftp_passive_port_max + 1))))
        if len(ports_unique) > 5:
            raise SystemExit(
                "ACI supports at most 5 public ports per container group. "
                f"Your FTP port config would expose {len(ports_unique)} ports. "
                "Set FTP_PASSIVE_PORT_MAX so the passive range is <= 4 ports (e.g. 50000-50003), "
                "or deploy to a platform that supports larger port ranges."
            )

        try:
            yaml_text = generate_deploy_yaml(
                name=name,
                location=location,
                image=image,
                registry_server=registry_server,
                registry_username=registry_username,
                registry_password=registry_password,
                identity_id=identity_id,
                identity_client_id=identity_client_id,
                identity_tenant_id=identity_tenant_id,
                storage_name=storage_name,
                storage_key=storage_key,
                kv_name=kv_name,
                dns_label=dns_label,
                cpu_cores=cpu_cores,
                memory_gb=memory_gb,
                data_share_name=data_share_name,
                ftp_port=ftp_port,
                ftp_passive_port_min=ftp_passive_port_min,
                ftp_passive_port_max=ftp_passive_port_max,
            )
        except ValueError as e:
            raise SystemExit(f"[deploy] Invalid ACI configuration: {e}")

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(yaml_text)
        yaml_path = f.name

    print(f"📝 [deploy] wrote: {yaml_path}")

    # Retry container creation with exponential backoff for transient registry errors
    max_retries = 5
    base_delay = 10.0  # seconds
    for attempt in range(1, max_retries + 1):
        try:
            run_az_command(["container", "create", "--resource-group", rg, "--file", yaml_path], capture_output=False)
            break  # Success
        except subprocess.CalledProcessError as e:
            err = getattr(e, "stderr", "") or ""
            # Check if it's a transient registry conflict error or generic registry error
            # Examples:
            # - 'Conflict':'RegistryErrorResponse'
            # - (RegistryErrorResponse) An error response is received from the docker registry
            is_transient = "RegistryErrorResponse" in err or "Conflict" in err
            if is_transient and attempt < max_retries:
                sleep_time = min(60.0, base_delay * (2 ** (attempt - 1)))
                print(f"⚠️  [deploy] Registry conflict (attempt {attempt}/{max_retries}). Retrying in {sleep_time:.0f}s...")
                time.sleep(sleep_time)
            else:
                # Not a transient error or out of retries
                raise

    print("\n[done] Deployed.")
    print(f"  FQDN: {dns_label}.{location}.azurecontainer.io")
    if service == "web":
        print(f"  http://{dns_label}.{location}.azurecontainer.io")
    elif service == "web-caddy":
        print(f"  https://{public_domain}")
    else:
        print(f"  ftp://{dns_label}.{location}.azurecontainer.io:{ftp_port}")



if __name__ == "__main__":
    raise SystemExit(main())
