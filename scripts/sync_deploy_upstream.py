#!/usr/bin/env python3
"""Sync the protected-azure-container deploy engine submodule.

This repo keeps the upstream deploy engine as a git submodule at:
  scripts/deploy/_upstream

Typical usage:
  python3 scripts/sync_deploy_upstream.py --init
  python3 scripts/sync_deploy_upstream.py --update

Notes:
- Submodules are pinned by design (reproducible). `--update` moves the pin.
- This is a convenience wrapper around `git submodule update ...`.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

SUBMODULE_PATH = "scripts/deploy/_upstream"


def _run(repo_root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo_root), check=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Sync deploy engine submodule")
    parser.add_argument(
        "--init",
        action="store_true",
        help="Initialize submodules (git submodule update --init --recursive)",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help=f"Resync upstream (git submodule update --remote --merge {SUBMODULE_PATH})",
    )

    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]

    if args.init:
        _run(repo_root, "submodule", "update", "--init", "--recursive")

    if args.update:
        _run(repo_root, "submodule", "update", "--remote", "--merge", SUBMODULE_PATH)

    if not args.init and not args.update:
        _run(repo_root, "submodule", "update", "--remote", "--merge", SUBMODULE_PATH)


if __name__ == "__main__":
    main()
