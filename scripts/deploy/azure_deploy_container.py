#!/usr/bin/env python3
"""Run the upstream protected-azure-container deploy engine against this repo.

Upstream engine is vendored as a git submodule at:
  scripts/deploy/_upstream

This wrapper keeps the user-facing entrypoint in scripts/deploy/ while allowing
fast upstream resyncs via submodule updates.

It also cooperates with this repo's deploy hooks at:
  scripts/deploy/deploy_customizations.py
"""

from __future__ import annotations

import importlib.util
import sys
import time as time
from pathlib import Path

try:
    from scripts.deploy import docker_compose_helpers as compose_helpers  # type: ignore
except ImportError:
    import docker_compose_helpers as compose_helpers  # type: ignore


_DEFAULT = object()

# These attributes exist primarily so unit tests can monkeypatch them on this
# module. When `main()` runs, any monkeypatched values are copied into the
# upstream engine module before executing it.
az_logged_in = _DEFAULT
ensure_infra = _DEFAULT
get_storage_key = _DEFAULT
get_identity_details = _DEFAULT
ensure_oidc_app_and_sp = _DEFAULT
ensure_oidc_app_role_assignment = _DEFAULT
run_az_command = _DEFAULT
docker_pull = _DEFAULT
docker_push = _DEFAULT
docker_build = _DEFAULT
docker_login = _DEFAULT
kv_secret_get = _DEFAULT
kv_secret_set = _DEFAULT


def _ensure_infra_quota_scoped_to_data_share(
    *,
    resource_group: str,
    location: str,
    container_name: str,
    identity_name: str,
    keyvault_name: str,
    storage_name: str,
    shares: list[str],
    file_share_quota_gb: int = 5,
) -> None:
    """Ensure infra but only apply `file_share_quota_gb` to the FTP data share.

    Upstream deploy engine uses AZURE_FILE_SHARE_QUOTA_GB for *all* file shares
    it ensures (workspace/caddy). For camera-storage-viewer we want that env var
    to only control the durable /data share quota (i.e. <container>-data).
    """

    import azure_deploy_container_helpers as helpers  # type: ignore

    helpers.run_az_command(["account", "show", "--output", "none"], capture_output=False)

    helpers.ensure_resource_group(resource_group=resource_group, location=location)

    identity = helpers.ensure_managed_identity(name=identity_name, resource_group=resource_group)
    storage = helpers.ensure_storage_account(name=storage_name, resource_group=resource_group, location=location)
    kv = helpers.ensure_key_vault(name=keyvault_name, resource_group=resource_group, location=location)

    subscription_id = str(
        helpers.run_az_command(["account", "show", "--query", "id", "-o", "tsv"], capture_output=True)
    ).strip()

    identity_object_id = str(identity.get("principalId") or "").strip()
    if not identity_object_id:
        raise RuntimeError("Managed identity missing principalId")

    helpers.ensure_role_assignments(
        subscription_id=subscription_id,
        resource_group=resource_group,
        identity_object_id=identity_object_id,
        keyvault_name=str(kv.get("name") or keyvault_name),
        storage_account_name=str(storage.get("name") or storage_name),
    )

    data_share_default = f"{container_name}-data"
    for share in shares:
        quota_gb = file_share_quota_gb if share == data_share_default else 5
        helpers.ensure_file_share_exists(
            account_name=storage_name,
            share_name=share,
            resource_group=resource_group,
            quota_gb=quota_gb,
        )


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
    public_domain: str,
    acme_email: str,
    basic_auth_user: str,
    basic_auth_hash: str,
    app_cpu_cores: float,
    app_memory_gb: float,
    share_workspace: str,
    data_share_name: str | None = None,
    caddy_data_share_name: str = "",
    caddy_config_share_name: str = "",
    caddy_image: str = "",
    caddy_cpu_cores: float = 0.5,
    caddy_memory_gb: float = 0.5,
    app_port: int = 8080,
    app_ports: list[int] | None = None,
    app_command: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
    other_image: str | None = None,
    other_cpu_cores: float = 0.5,
    other_memory_gb: float = 0.5,
    restart_policy: str = "OnFailure",
) -> str:
    """Back-compat helper for tests/external callers.

    The deploy entrypoint is now the upstream engine, but this repo still keeps
    its YAML rendering helpers in `scripts/deploy/azure_deploy_yaml_helpers.py`.
    """

    try:
        from scripts.deploy import azure_deploy_yaml_helpers as yaml_helpers  # type: ignore
    except ImportError:
        import azure_deploy_yaml_helpers as yaml_helpers  # type: ignore

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
        public_domain=public_domain,
        acme_email=acme_email,
        basic_auth_user=basic_auth_user,
        basic_auth_hash=basic_auth_hash,
        app_cpu_cores=app_cpu_cores,
        app_memory_gb=app_memory_gb,
        share_workspace=share_workspace,
        data_share_name=data_share_name,
        caddy_data_share_name=caddy_data_share_name,
        caddy_config_share_name=caddy_config_share_name,
        caddy_image=caddy_image,
        caddy_cpu_cores=caddy_cpu_cores,
        caddy_memory_gb=caddy_memory_gb,
        app_port=app_port,
        app_ports=app_ports,
        app_command=app_command,
        extra_env=extra_env,
        other_image=other_image,
        other_cpu_cores=other_cpu_cores,
        other_memory_gb=other_memory_gb,
        restart_policy=restart_policy,
    )


def _argv_has_flag(argv: list[str], flag: str) -> bool:
    return flag in argv or any(a.startswith(flag + "=") for a in argv)


def _propagate_test_overrides(*, upstream_engine) -> None:
    # Compose/time are patched as objects/modules, not sentinels.
    upstream_engine.compose_helpers = compose_helpers
    upstream_engine.time = time

    for name in [
        "az_logged_in",
        "ensure_infra",
        "get_storage_key",
        "get_identity_details",
        "ensure_oidc_app_and_sp",
        "ensure_oidc_app_role_assignment",
        "run_az_command",
        "docker_pull",
        "docker_push",
        "docker_build",
        "docker_login",
        "kv_secret_get",
        "kv_secret_set",
    ]:
        val = globals().get(name, _DEFAULT)
        if val is not _DEFAULT:
            setattr(upstream_engine, name, val)


def main(argv: list[str] | None = None, repo_root_override: Path | None = None) -> None:
    engine_repo_root = Path(__file__).resolve().parents[2]
    repo_root = repo_root_override or engine_repo_root
    upstream_deploy_dir = engine_repo_root / "scripts" / "deploy" / "_upstream" / "scripts" / "deploy"

    if not upstream_deploy_dir.exists():
        raise SystemExit(
            "Upstream deploy engine submodule not present. "
            "Run: git submodule update --init --recursive"
        )

    # Ensure we import upstream's top-level modules (env_schema, helpers, etc)
    # even if similarly-named modules were imported earlier from this repo.
    for key in [
        "env_schema",
        "azure_deploy_container_helpers",
        "azure_deploy_yaml_helpers",
        "docker_compose_helpers",
        "deploy_hooks",
    ]:
        sys.modules.pop(key, None)

    # Force-load upstream env_schema under the canonical name `env_schema`.
    # This makes downstream wrappers and upstream engine agree on the module.
    env_schema_path = upstream_deploy_dir / "env_schema.py"
    spec = importlib.util.spec_from_file_location("env_schema", env_schema_path)
    if spec and spec.loader:
        module = importlib.util.module_from_spec(spec)
        sys.modules["env_schema"] = module
        spec.loader.exec_module(module)

        # Downstream extension: camera-storage-viewer has additional runtime/deploy
        # keys. Instead of rewriting `.env`/`.env.deploy` (and creating `*.full`
        # copies), extend the upstream schema at runtime so strict validation
        # accepts viewer-specific keys.
        try:
            env_schema = module

            class _RawKey:
                def __init__(self, value: str) -> None:
                    self.value = value

            def _extend_schema(*, attr: str, keys: set[str], target) -> None:
                schema = tuple(getattr(env_schema, attr, ()) or ())
                existing = {spec.key.value for spec in schema}
                extra_specs = []
                for k in sorted(keys):
                    if k in existing:
                        continue
                    extra_specs.append(
                        env_schema.EnvKeySpec(
                            key=_RawKey(k),
                            mandatory=False,
                            default=None,
                            targets=frozenset({target}),
                        )
                    )
                if extra_specs:
                    setattr(env_schema, attr, schema + tuple(extra_specs))

            # Load this repo's schema (under a unique name) to derive the
            # camera-storage-viewer key set.
            local_schema_path = (repo_root / "scripts" / "deploy" / "env_schema.py").resolve()
            local_module_name = "_csv_local_env_schema"
            local_spec = importlib.util.spec_from_file_location(local_module_name, local_schema_path)
            local_module = None
            if local_spec and local_spec.loader and local_schema_path.exists():
                local_module = importlib.util.module_from_spec(local_spec)
                # Python 3.12 dataclasses can consult sys.modules during class
                # creation; ensure the module is present while executing.
                sys.modules[local_module_name] = local_module
                local_spec.loader.exec_module(local_module)

            runtime_var_keys: set[str] = set()
            deploy_var_keys: set[str] = set()
            runtime_secret_keys: set[str] = set()

            if local_module is not None:
                # Runtime vars: allow everything this repo considers a runtime var.
                for spec_item in getattr(local_module, "RUNTIME_SCHEMA", ()) or ():
                    key = getattr(spec_item, "key", None)
                    if key is None:
                        continue
                    if isinstance(key, getattr(local_module, "VarsEnum")):
                        runtime_var_keys.add(str(key.value))
                    elif isinstance(key, getattr(local_module, "SecretsEnum")):
                        # Treat local runtime secrets as `.env.secrets` keys upstream.
                        runtime_secret_keys.add(str(key.value))

                # Deploy vars: allow everything this repo considers a deploy var.
                for spec_item in getattr(local_module, "DEPLOY_SCHEMA", ()) or ():
                    key = getattr(spec_item, "key", None)
                    if key is None:
                        continue
                    if isinstance(key, getattr(local_module, "VarsEnum")):
                        deploy_var_keys.add(str(key.value))

            # Ensure any compose-referenced runtime env vars are allowed, even if
            # they are not part of the schema (compose is the source of truth).
            runtime_var_keys.add("AZURE_KEYVAULT_URI")

            # Avoid misclassifying deploy-only secrets (e.g. GHCR_TOKEN) as runtime secrets.
            runtime_secret_keys = {
                k
                for k in runtime_secret_keys
                if k.startswith("FTP_") or k in {"BASIC_AUTH_HASH", "APP_SECRET"}
            }

            _extend_schema(attr="RUNTIME_SCHEMA", keys=runtime_var_keys, target=env_schema.EnvTarget.DOTENV_RUNTIME)
            _extend_schema(attr="DEPLOY_SCHEMA", keys=deploy_var_keys, target=env_schema.EnvTarget.DOTENV_DEPLOY)
            _extend_schema(attr="SECRETS_SCHEMA", keys=runtime_secret_keys, target=env_schema.EnvTarget.DOTENV_SECRETS)
        except Exception:
            # Best-effort; if upstream schema shape changes, we fall back to hooks.
            pass

    argv_list = list(argv if argv is not None else sys.argv[1:])

    # Default behavior: only use AZURE_FILE_SHARE_QUOTA_GB for the durable data share
    # (<container>-data). Avoid resizing upstream workspace/caddy shares.
    global ensure_infra
    if ensure_infra is _DEFAULT:
        ensure_infra = _ensure_infra_quota_scoped_to_data_share

    # This repo's Dockerfile lives at docker/Dockerfile. The upstream engine defaults
    # to `Dockerfile` in the context root unless --dockerfile is provided.
    if not _argv_has_flag(argv_list, "--dockerfile"):
        candidate = engine_repo_root / "docker" / "Dockerfile"
        if candidate.exists():
            argv_list.extend(["--dockerfile", str(candidate)])

    # With upstream's secrets-split model (.env + .env.secrets), it is safe and
    # expected for `.env` to contain runtime (non-secret) config for the app.
    # Upload the full runtime env file content so camera-storage-viewer keys
    # (FTP_*, OUT_DIR, etc) are available at runtime.
    if not _argv_has_flag(argv_list, "--upload-env-raw"):
        argv_list.append("--upload-env-raw")

    sys.path.insert(0, str(upstream_deploy_dir))
    import azure_deploy_container as upstream_engine  # type: ignore

    _propagate_test_overrides(upstream_engine=upstream_engine)
    upstream_engine.main(argv_list, repo_root_override=repo_root)


if __name__ == "__main__":
    main(sys.argv[1:])
