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

import sys
import time as time
import importlib.util
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


def generate_deploy_yaml(**kwargs) -> str:
    """Backwards-compatible helper for unit tests.

    The deploy entrypoint is now the upstream engine, but this repo still keeps
    its YAML rendering helpers in `scripts/deploy/azure_deploy_yaml_helpers.py`.
    """

    try:
        from scripts.deploy import azure_deploy_yaml_helpers as yaml_helpers  # type: ignore
    except ImportError:
        import azure_deploy_yaml_helpers as yaml_helpers  # type: ignore
    return yaml_helpers.generate_deploy_yaml(**kwargs)


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

    env_schema_path = upstream_deploy_dir / "env_schema.py"
    spec = importlib.util.spec_from_file_location("env_schema", env_schema_path)
    if spec and spec.loader:
        module = importlib.util.module_from_spec(spec)
        sys.modules["env_schema"] = module
        spec.loader.exec_module(module)

    argv_list = list(argv if argv is not None else sys.argv[1:])

    # This repo's Dockerfile lives at docker/Dockerfile. The upstream engine defaults
    # to `Dockerfile` in the context root unless --dockerfile is provided.
    # Add a sensible default only when the user hasn't specified one.
    if not _argv_has_flag(argv_list, "--dockerfile"):
        candidate = engine_repo_root / "docker" / "Dockerfile"
        if candidate.exists():
            argv_list.extend(["--dockerfile", str(candidate)])

    # Preserve full runtime env for upload while hooks slim .env for strict validation.
    runtime_env_path = repo_root / ".env"
    full_env_path = repo_root / ".env.full"
    if runtime_env_path.exists() and not full_env_path.exists():
        try:
            full_env_path.write_text(runtime_env_path.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            # Best-effort; hooks will also attempt to create/restore.
            pass

    if runtime_env_path.exists() and not _argv_has_flag(argv_list, "--upload-env-file"):
        argv_list.extend(["--upload-env-file", str(full_env_path)])

    # Ensure FTP_* keys make it into Key Vault/runtime. BASIC_AUTH_* stays for Caddy.
    if not _argv_has_flag(argv_list, "--upload-env-prefixes"):
        argv_list.extend(["--upload-env-prefixes", "BASIC_AUTH_,FTP_"])

    sys.path.insert(0, str(upstream_deploy_dir))
    import azure_deploy_container as upstream_engine  # type: ignore

    _propagate_test_overrides(upstream_engine=upstream_engine)

    upstream_engine.main(argv_list, repo_root_override=repo_root)


if __name__ == "__main__":
    main()
