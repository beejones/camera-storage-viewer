#!/usr/bin/env python3
"""Validate `.env` / `.env.deploy` against the deterministic schema.

This is intended to run:
- locally (before deploy)
- in CI (before invoking scripts/deploy/azure_deploy_container.py)

Strict by default:
- unknown keys => error
- legacy alias keys => error
- missing mandatory keys => error
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Add scripts dir to path to allow importing sibling modules when running as a script.
sys.path.append(str(Path(__file__).parent))

from env_schema import (
    DEPLOY_SCHEMA,
    RUNTIME_SCHEMA,
    EnvTarget,
    EnvValidationError,
    VarsEnum,
    apply_defaults,
    filter_schema_by_targets,
    parse_dotenv_file,
    validate_cross_field_rules,
    validate_known_keys,
    validate_required,
)


def _env_subset(schema_keys: set[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k in schema_keys:
        v = os.getenv(k)
        if v is None:
            continue
        v = str(v).strip()
        if not v:
            continue
        out[k] = v
    return out


def _validate_runtime(runtime_path: Path | None) -> None:
    context = "runtime (.env)"
    kv: dict[str, str] = {}

    if runtime_path is not None:
        if not runtime_path.exists():
            raise SystemExit(f"[env] Missing runtime env file: {runtime_path}")
        kv = parse_dotenv_file(runtime_path)
        validate_known_keys(RUNTIME_SCHEMA, kv, context=context)

    kv = apply_defaults(RUNTIME_SCHEMA, kv)
    validate_required(RUNTIME_SCHEMA, kv, context=context)


def _validate_deploy(deploy_path: Path | None) -> None:
    context = "deploy (.env.deploy + env)"

    # `.env.deploy` is optional in CI if env vars are set directly.
    file_kv: dict[str, str] = {}
    if deploy_path is not None and deploy_path.exists():
        file_kv = parse_dotenv_file(deploy_path)
        validate_known_keys(DEPLOY_SCHEMA, file_kv, context=context)

    # Overlay process env (CI) on top of file values.
    schema_keys = {spec.key.value for spec in DEPLOY_SCHEMA}
    env_kv = _env_subset(schema_keys)

    merged = dict(file_kv)
    merged.update(env_kv)

    merged = apply_defaults(DEPLOY_SCHEMA, merged)

    # Provide DNS label default derived from container name if unset.
    if not merged.get(VarsEnum.AZURE_DNS_LABEL.value):
        merged[VarsEnum.AZURE_DNS_LABEL.value] = merged.get(VarsEnum.AZURE_CONTAINER_NAME.value, "").strip()

    # Only require keys that are meant to live in `.env.deploy`.
    # (E.g. GH Actions meta-secret RUNTIME_ENV_DOTENV is mandatory for CI wiring,
    # but it is not expected to be present in `.env.deploy`.)
    deploy_dotenv_specs = filter_schema_by_targets(DEPLOY_SCHEMA, include={EnvTarget.DOTENV_DEPLOY})
    validate_required(deploy_dotenv_specs, merged, context=context)
    validate_cross_field_rules(deploy_kv=merged, context=context)


def main() -> None:
    ap = argparse.ArgumentParser(description="Validate .env and .env.deploy against schema")
    ap.add_argument("--runtime", default=".env", help="Path to runtime env file (default: .env)")
    ap.add_argument("--deploy", default=".env.deploy", help="Path to deploy env file (default: .env.deploy)")
    ap.add_argument(
        "--no-runtime-file",
        action="store_true",
        help="Skip reading/validating the runtime env file (useful in CI if you validate only deploy inputs)",
    )
    ap.add_argument(
        "--no-deploy-file",
        action="store_true",
        help="Skip reading/validating the deploy env file (useful if you rely on process env vars)",
    )

    args = ap.parse_args()

    runtime_path = None if args.no_runtime_file else Path(args.runtime).expanduser().resolve()
    deploy_path = None if args.no_deploy_file else Path(args.deploy).expanduser().resolve()

    try:
        _validate_runtime(runtime_path)
        _validate_deploy(deploy_path)
    except EnvValidationError as e:
        print(e.format(), file=sys.stderr)
        raise SystemExit(2)

    print("[env] ok")


if __name__ == "__main__":
    raise SystemExit(main())
