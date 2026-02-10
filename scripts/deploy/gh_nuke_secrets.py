#!/usr/bin/env python3
"""Wrapper for the upstream GitHub Actions nuke script.

Upstream engine is vendored as a git submodule at:
  scripts/deploy/_upstream

This wrapper exists because the upstream deploy engine expects to find
`scripts/deploy/gh_nuke_secrets.py` in the *downstream* repo root.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None, repo_root_override: Path | None = None) -> None:
    repo_root = repo_root_override or Path(__file__).resolve().parents[2]
    upstream_deploy_dir = repo_root / "scripts" / "deploy" / "_upstream" / "scripts" / "deploy"

    if not upstream_deploy_dir.exists():
        raise SystemExit(
            "Upstream deploy engine submodule not present. "
            "Run: git submodule update --init --recursive"
        )

    sys.path.insert(0, str(upstream_deploy_dir))
    import gh_nuke_secrets as upstream_script  # type: ignore

    sys.argv = [sys.argv[0], *([] if argv is None else argv)]
    upstream_script.main()


if __name__ == "__main__":
    main(sys.argv[1:])
