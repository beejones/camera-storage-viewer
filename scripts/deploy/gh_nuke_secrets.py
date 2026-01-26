#!/usr/bin/env python3
"""
⚠️  DANGER ZONE ⚠️

Rubbish/Nuke script to DELETE ALL GitHub Actions Secrets and Variables
from the detected repository (and its environments).

Usage:
    python3 scripts/deploy/gh_nuke_secrets.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Add scripts dir to path
sys.path.append(str(Path(__file__).parent.parent))

def _run(cmd: list[str], *, ignore_errors: bool = False) -> str:
    p = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if p.returncode != 0:
        if ignore_errors:
            return ""
        cmd_str = " ".join(cmd)
        raise SystemExit(f"Command failed ({p.returncode}): {cmd_str}\n{p.stderr.strip()}")
    return p.stdout.strip()

def _detect_repo() -> str:
    # Try to resolve 'origin' remote first to avoid defaulting to upstream in forks.
    try:
        origin_url = _run(["git", "remote", "get-url", "origin"], ignore_errors=True).strip()
        if origin_url:
            return _run(["gh", "repo", "view", origin_url, "--json", "nameWithOwner", "-q", ".nameWithOwner"]).strip()
    except Exception:
        pass
    
    # Fallback to default detection (uses current directory context)
    return _run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"]).strip()

def get_items(repo: str, kind: str, scope_args: list[str]) -> list[str]:
    """Get list of secret/variable names."""
    # kind is 'secret' or 'variable'
    # scope_args e.g. ["-R", repo] or ["-R", repo, "--env", "production"]
    
    cmd = ["gh", kind, "list"] + scope_args + ["--json", "name", "-q", ".[].name"]
    out = _run(cmd, ignore_errors=True)
    if not out:
        return []
    return out.splitlines()

def delete_item(repo: str, kind: str, name: str, scope_args: list[str], dry_run: bool):
    label = f"{kind} {name}"
    ctx = "repo"
    if "--env" in scope_args:
        ctx = f"env:{scope_args[scope_args.index('--env') + 1]}"
    
    if dry_run:
        print(f"[dry-run] would delete {label} ({ctx})")
        return

    print(f"🔥 Deleting {label} ({ctx})...")
    # gh secret delete NAME -R repo
    cmd = ["gh", kind, "delete", name] + scope_args
    _run(cmd, ignore_errors=True)

def main():
    parser = argparse.ArgumentParser(description="Delete ALL GitHub Actions secrets/vars")
    parser.add_argument("--repo", default=None, help="Target repo (owner/repo)")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation (DANGEROUS)")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be deleted without doing it")
    args = parser.parse_args()

    repo = args.repo or _detect_repo()
    if not repo:
        raise SystemExit("Could not detect repository. Run from a git repo or pass --repo.")

    print(f"🎯 Target Repository: {repo}")
    
    if not args.yes and not args.dry_run:
        print("\n" + "!" * 60)
        print("⚠️  WARNING: THIS WILL DELETE ALL ACTIONS SECRETS AND VARIABLES ⚠️")
        print("!" * 60)
        print(f"Target: {repo}")
        print("Includes:")
        print("  - Repository Secrets")
        print("  - Repository Variables")
        print("  - Environment Secrets (all environments)")
        print("  - Environment Variables (all environments)")
        print("\nType 'DELETE' to continue:")
        confirm = input("> ").strip()
        if confirm != "DELETE":
            print("Aborted.")
            sys.exit(1)

    # 1. Repository Level
    repo_secrets = get_items(repo, "secret", ["-R", repo])
    repo_vars = get_items(repo, "variable", ["-R", repo])

    # 2. Environment Level
    # List environments first
    envs_json = _run(["gh", "api", f"repos/{repo}/environments", "--jq", ".environments[].name"], ignore_errors=True)
    envs = [e for e in envs_json.splitlines() if e.strip()]

    print(f"\nFound {len(repo_secrets)} secrets, {len(repo_vars)} vars in repo.")
    print(f"Found environments: {', '.join(envs) if envs else 'None'}")

    # Delete Repo Items
    for s in repo_secrets:
        delete_item(repo, "secret", s, ["-R", repo], args.dry_run)
    for v in repo_vars:
        delete_item(repo, "variable", v, ["-R", repo], args.dry_run)

    # Delete Environment Items
    for env in envs:
        print(f"\nScanning environment: {env}")
        env_secrets = get_items(repo, "secret", ["-R", repo, "--env", env])
        env_vars = get_items(repo, "variable", ["-R", repo, "--env", env])
        
        for s in env_secrets:
            delete_item(repo, "secret", s, ["-R", repo, "--env", env], args.dry_run)
        for v in env_vars:
            delete_item(repo, "variable", v, ["-R", repo, "--env", env], args.dry_run)

    print("\n✅ Done. All clean.")

if __name__ == "__main__":
    main()
