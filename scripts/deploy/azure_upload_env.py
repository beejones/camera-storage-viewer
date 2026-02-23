#!/usr/bin/env python3
"""Upload a local .env file to Azure Key Vault.

Stores the entire .env file content as a single Key Vault secret (default name: 'env').
This matches the protected-container ACI startup flow in scripts/azure_start.py.

Prereqs:
- az login
- Key Vault exists

Example:
    python scripts/deploy/azure_upload_env.py --vault <kv-name> --env-file .env
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Add scripts dir to path to allow importing azure_utils
sys.path.append(str(Path(__file__).parent))
try:
    from azure_utils import kv_secret_set_quiet
except ImportError:
    # Fallback if running from root without scripts in pythonpath
    sys.path.append("scripts")
    from azure_utils import kv_secret_set_quiet

from env_schema import (
    RUNTIME_SCHEMA,
    EnvValidationError,
    apply_defaults,
    parse_dotenv_file,
    validate_known_keys,
    validate_required,
)





def _upload_env_to_keyvault(*, vault_name: str, env_file: Path, secret_name: str) -> None:
    if not env_file.exists():
        print(f"[error] File not found: {env_file}")
        raise SystemExit(1)

    # Validate env file against strict runtime schema.
    try:
        kv = parse_dotenv_file(env_file)
        validate_known_keys(RUNTIME_SCHEMA, kv, context=f"runtime ({env_file.name})")
        kv = apply_defaults(RUNTIME_SCHEMA, kv)
        validate_required(RUNTIME_SCHEMA, kv, context=f"runtime ({env_file.name})")
    except EnvValidationError as e:
        print(e.format(), file=sys.stderr)
        raise SystemExit(2)

    content = env_file.read_text()
    lines = [l for l in content.splitlines() if l.strip() and not l.strip().startswith("#")]
    print(f"[upload] Read {env_file} ({len(lines)} non-comment lines)")

    try:
        kv_secret_set_quiet(vault_name=vault_name, secret_name=secret_name, value=content)
    except Exception as e:
        print("[error] Failed to upload secret")
        print(f"{e}")
        raise SystemExit(1)

    print(f"[upload] Uploaded secret '{secret_name}' to Key Vault '{vault_name}'")


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload .env file to Azure Key Vault")
    parser.add_argument("--vault", "-v", required=True, help="Key Vault name")
    parser.add_argument("--env-file", "-e", default=".env", help="Path to .env file")
    parser.add_argument("--secret-name", "-s", default="env", help="Secret name in Key Vault")
    args = parser.parse_args()

    # _check_azure_login removed as it is implicit in kv_secret_set_quiet / redundant
    _upload_env_to_keyvault(vault_name=args.vault, env_file=Path(args.env_file), secret_name=args.secret_name)


if __name__ == "__main__":
    raise SystemExit(main())
