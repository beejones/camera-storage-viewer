#!/usr/bin/env python3
"""Generate a Caddy-compatible bcrypt hash for Basic Auth.

This avoids putting plaintext passwords on the command line.

Usage:
  ./scripts/deploy/generate_bcrypt_hash.py
  ./scripts/deploy/generate_bcrypt_hash.py --cost 14
  ./scripts/deploy/generate_bcrypt_hash.py --compose-escape

Notes:
- docker-compose uses `$` for interpolation; to store bcrypt hashes in compose
  YAML safely, you often need to replace `$` with `$$`.
- For Azure deployments, `scripts/deploy/azure_deploy_container.py` normalizes
  `$$2...` back to `$2...` automatically.
"""

from __future__ import annotations

import argparse
import getpass

from scripts.deploy.azure_deploy_container_helpers import bcrypt_hash_password


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate bcrypt hash for Caddy basicauth")
    parser.add_argument("--cost", type=int, default=14, help="bcrypt cost factor (4-31). Default: 14")
    parser.add_argument(
        "--compose-escape",
        action="store_true",
        help="Replace '$' with '$$' for safe inclusion in docker-compose YAML",
    )
    args = parser.parse_args()

    password = getpass.getpass("Basic Auth password: ").strip()
    if not password:
        raise SystemExit("Password must be non-empty")

    hashed = bcrypt_hash_password(password, cost=args.cost)
    if args.compose_escape:
        hashed = hashed.replace("$", "$$")

    print(hashed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
