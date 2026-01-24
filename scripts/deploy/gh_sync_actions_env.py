#!/usr/bin/env python3
"""Sync local .env files into GitHub Actions variables/secrets.

Primary use-case: make it easy to run the deploy workflow in CI.

It supports two approaches:

1) Store runtime dotenv as a single secret (recommended):
    - RUNTIME_ENV_DOTENV -> contents of .env (runtime inputs; uploaded to KV by azure_deploy_container.py)

2) Sync per-key variables/secrets from .env.deploy (deploy-time inputs).

Requirements:
- GitHub CLI installed and authenticated (gh auth login)
- Permission to set Actions secrets/vars on the repo

Examples:
    python3 scripts/deploy/gh_sync_actions_env.py --set
    python3 scripts/deploy/gh_sync_actions_env.py --set --also-sync-keys
    python3 scripts/deploy/gh_sync_actions_env.py --repo owner/repo --set
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import io
import hashlib
import sys
from pathlib import Path

from dotenv import dotenv_values

from env_schema import (
    DEPLOY_SCHEMA,
    RUNTIME_SCHEMA,
    EnvTarget,
    EnvValidationError,
    SecretsEnum,
    VarsEnum,
    apply_defaults,
    parse_dotenv_file,
    truthy,
    validate_cross_field_rules,
    validate_known_keys,
    validate_required,
)

# Add scripts dir to path to allow importing azure_utils
sys.path.append(str(Path(__file__).parent))
try:
    from azure_utils import run_az_command, get_az_account_info, get_app_client_id_by_display_name
except ImportError:
    sys.path.append("scripts")
    from azure_utils import run_az_command, get_az_account_info, get_app_client_id_by_display_name


REQUIRED_FOR_AZURE_LOGIN = {
    VarsEnum.AZURE_CLIENT_ID.value,
    VarsEnum.AZURE_TENANT_ID.value,
    VarsEnum.AZURE_SUBSCRIPTION_ID.value,
}


def _supports_color() -> bool:
    # Respect https://no-color.org/
    if "NO_COLOR" in os.environ:
        return False
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def _color(text: str, code: str) -> str:
    if not _supports_color():
        return text
    return f"\x1b[{code}m{text}\x1b[0m"


def _fmt_kv(name: str, value: str) -> str:
    # Key cyan, value green.
    return f"{_color(name, '36')}={_color(value, '32')}"


def _run(cmd: list[str], *, input_text: str | None = None) -> str:
    def _format_cmd(argv: list[str]) -> str:
        # Avoid leaking secret values in error messages (e.g. `gh secret set ... -b <value>`).
        redacted: list[str] = []
        redact_next = False
        for a in argv:
            if redact_next:
                redacted.append("***")
                redact_next = False
                continue
            redacted.append(a)
            if a in {"-b", "--body"}:
                redact_next = True
        return " ".join(redacted)

    p = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if p.returncode != 0:
        raise SystemExit(f"Command failed ({p.returncode}): {_format_cmd(cmd)}\n{p.stderr.strip()}")
    return p.stdout.strip()


def _detect_repo() -> str:
    # Prefer 'origin' remote to avoid defaulting to upstream in forks.
    try:
        origin_url = _run(["git", "remote", "get-url", "origin"]).strip()
        if origin_url:
            return _run(["gh", "repo", "view", origin_url, "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    except Exception:
        pass

    # Next try `gh repo view` in the current directory (handles detached dirs).
    try:
        return _run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    except SystemExit:
        pass

    # Last resort: parse the git remote ourselves.
    repo = _detect_repo_from_git_remote()
    if repo:
        return repo

    raise SystemExit(
        "Could not detect GitHub repo for this directory. "
        "Run `gh repo set-default` or pass --repo owner/repo."
    )


def _detect_repo_from_git_remote() -> str | None:
    try:
        url = _run(["git", "remote", "get-url", "origin"]).strip()
    except Exception:
        return None

    # Common forms:
    # - git@github.com:owner/repo.git
    # - https://github.com/owner/repo.git
    # - ssh://git@github.com/owner/repo.git
    m = re.search(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$", url)
    if not m:
        return None
    owner = (m.group("owner") or "").strip()
    repo = (m.group("repo") or "").strip()
    if not owner or not repo:
        return None
    return f"{owner}/{repo}"


def _detect_default_branch(repo: str) -> str | None:
    try:
        return _run(["gh", "repo", "view", repo, "--json", "defaultBranchRef", "-q", ".defaultBranchRef.name"])
    except Exception:
        return None


def _detect_current_branch() -> str | None:
    try:
        branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip()
    except Exception:
        return None
    if not branch or branch == "HEAD":
        return None
    return branch


def _az_single_app_client_id() -> str | None:
    res = run_az_command(
        ["ad", "app", "list", "--query", "[].appId", "-o", "json"],
        capture_output=True,
        ignore_errors=True,
    )
    if isinstance(res, list):
        if len(res) == 1:
            return str(res[0])
    return None


def _az_federated_credentials(app_id: str) -> list[dict]:
    res = run_az_command(
        [
            "ad",
            "app",
            "federated-credential",
            "list",
            "--id",
            app_id,
            "-o",
            "json",
        ],
        capture_output=True,
        ignore_errors=True,
    )
    if isinstance(res, list):
        return res
    return []


def _ensure_federated_credential(*, app_id: str, repo: str, subject: str) -> None:
    issuer = "https://token.actions.githubusercontent.com"
    audience = "api://AzureADTokenExchange"

    existing = _az_federated_credentials(app_id)
    for item in existing:
        if str(item.get("issuer")) != issuer:
            continue
        if str(item.get("subject")) != subject:
            continue
        audiences = item.get("audiences") or []
        if audience in audiences:
            return

    suffix = ""
    marker = f"repo:{repo}:ref:refs/heads/"
    if subject.startswith(marker):
        suffix = subject[len(marker) :].strip()
    if not suffix:
        suffix = hashlib.sha1(subject.encode("utf-8")).hexdigest()[:8]
    safe_repo = repo.replace("/", "-")
    safe_suffix = re.sub(r"[^a-zA-Z0-9-]+", "-", suffix)[:32].strip("-")
    name = f"github-oidc-{safe_repo}-{safe_suffix}" if safe_suffix else f"github-oidc-{safe_repo}-{hashlib.sha1(subject.encode('utf-8')).hexdigest()[:8]}"
    name = name[:120]
    
    print(f"[oidc] Creating federated credential: {name}")
    
    params = json.dumps(
        {
            "name": name,
            "issuer": issuer,
            "subject": subject,
            "audiences": [audience],
        }
    )
    run_az_command(
        [
            "ad",
            "app",
            "federated-credential",
            "create",
            "--id",
            app_id,
            "--parameters",
            params,
        ],
        capture_output=False,
    )


def _has_gh_variables() -> bool:
    # gh variable set is relatively new; probe support.
    p = subprocess.run(["gh", "variable", "set", "-h"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return p.returncode == 0


def _set_secret(*, repo: str, name: str, value: str, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] set secret {name} (repo={repo})")
        return
    _run(["gh", "secret", "set", name, "-R", repo, "-b", value])
    print(f"[ok] set secret {name} (repo={repo})")


def _set_variable(*, repo: str, name: str, value: str, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] set var {name} (repo={repo})")
        return

    # Prefer `gh variable set` when available.
    if _has_gh_variables():
        try:
            _run(["gh", "variable", "set", name, "-R", repo, "-b", value])
            print(f"[ok] set var {name} (repo={repo})")
            return
        except SystemExit:
            # Fall through to API fallback.
            pass

    # API fallback (PATCH then POST if missing)
    p = subprocess.run(
        [
            "gh",
            "api",
            "-X",
            "PATCH",
            f"repos/{repo}/actions/variables/{name}",
            "-f",
            f"name={name}",
            "-f",
            f"value={value}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if p.returncode == 0:
        print(f"[ok] set var {name} (repo={repo})")
        return

    err = (p.stderr or "").strip()
    if "HTTP 404" in err or "Not Found" in err:
        _run(
            [
                "gh",
                "api",
                "-X",
                "POST",
                f"repos/{repo}/actions/variables",
                "-f",
                f"name={name}",
                "-f",
                f"value={value}",
            ]
        )
        print(f"[ok] set var {name} (repo={repo})")
        return

    raise SystemExit(f"Command failed ({p.returncode}): gh api (set variable {name})\n{err}")


def _read_text(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"Missing file: {path}")
    return path.read_text(encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=None, help="GitHub repo in owner/repo form (default: current)")
    ap.add_argument("--deploy-env", default=".env.deploy", help="Path to deploy env file (default: .env.deploy)")
    ap.add_argument("--runtime-env", default=".env", help="Path to runtime env file (default: .env)")
    ap.add_argument(
        "--set",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Set secrets/vars (default: enabled; use --no-set for dry-run)",
    )
    ap.add_argument(
        "--only-files",
        action="store_true",
        help="Only set RUNTIME_ENV_DOTENV secret (no per-key sync)",
    )
    ap.add_argument(
        "--also-sync-keys",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Also sync keys from .env.deploy as GitHub Actions vars/secrets (default: enabled; use --no-also-sync-keys)",
    )
    ap.add_argument(
        "--auto-fill-azure-ids",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "If AZURE_TENANT_ID / AZURE_SUBSCRIPTION_ID are missing, try to read them from `az account show` "
            "(default: enabled; use --no-auto-fill-azure-ids to disable)."
        ),
    )
    ap.add_argument(
        "--auto-fill-azure-client-id",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "If AZURE_CLIENT_ID is missing, try to read it from `az ad app list` "
            "(default: enabled; use --no-auto-fill-azure-client-id to disable)."
        ),
    )
    ap.add_argument(
        "--azure-app-display-name",
        default="github-actions-aci-deploy",
        help=(
            "Optional: if AZURE_CLIENT_ID is missing, look it up by Azure AD App Registration display name "
            "using `az ad app list --display-name` (default: github-actions-aci-deploy)."
        ),
    )
    ap.add_argument(
        "--azure-client-id",
        default=None,
        help=(
            "Optional: explicitly set AZURE_CLIENT_ID (App Registration appId / client-id). "
            "This overrides the value from .env.deploy and avoids needing to look it up."
        ),
    )
    ap.add_argument(
        "--ensure-federated-credential",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Ensure GitHub OIDC federated credential exists on the Azure App Registration "
            "(default: enabled; use --no-ensure-federated-credential to disable)."
        ),
    )
    ap.add_argument(
        "--oidc-subject",
        default=None,
        help=(
            "Override the federated credential subject. Default is repo:<owner>/<repo>:ref:refs/heads/<default-branch>."
        ),
    )

    args = ap.parse_args()

    repo = args.repo or _detect_repo()
    dry_run = not bool(args.set)

    if dry_run:
        print("[dry-run] No changes will be made.")
        print("[dry-run] Re-run with: python3 scripts/deploy/gh_sync_actions_env.py --set")

    deploy_path = Path(args.deploy_env).expanduser().resolve()
    runtime_path = Path(args.runtime_env).expanduser().resolve()

    deploy_text = _read_text(deploy_path)
    runtime_text = _read_text(runtime_path)

    # Strict validation of dotenv inputs (no unknown keys, no legacy aliases).
    try:
        deploy_kv = parse_dotenv_file(deploy_path)
        runtime_kv = parse_dotenv_file(runtime_path)

        validate_known_keys(DEPLOY_SCHEMA, deploy_kv, context="deploy (.env.deploy)")
        validate_known_keys(RUNTIME_SCHEMA, runtime_kv, context="runtime (.env)")

        # Apply defaults and validate required keys for the dotenv portions.
        runtime_kv = apply_defaults(RUNTIME_SCHEMA, runtime_kv)
        validate_required(RUNTIME_SCHEMA, runtime_kv, context="runtime (.env)")

        # Allow deploy-script-derived values (e.g. AZURE_OIDC_APP_NAME) to satisfy schema validation
        # when this script is invoked as a subprocess from azure_deploy_container.py.
        deploy_schema_keys = {spec.key.value for spec in DEPLOY_SCHEMA}
        deploy_kv_env = {k: v for k, v in os.environ.items() if k in deploy_schema_keys and str(v).strip()}
        deploy_kv.update(deploy_kv_env)

        deploy_kv = apply_defaults(DEPLOY_SCHEMA, deploy_kv)
        # Only require keys that belong to deploy dotenv.
        deploy_dotenv_specs = [spec for spec in DEPLOY_SCHEMA if EnvTarget.DOTENV_DEPLOY in spec.targets]
        validate_required(deploy_dotenv_specs, deploy_kv, context="deploy (.env.deploy)")
        validate_cross_field_rules(deploy_kv=deploy_kv, context="deploy (.env.deploy)")
    except EnvValidationError as e:
        raise SystemExit(e.format())

    # Ensure required Azure login vars are present as GitHub Actions VARIABLES.
    azure_client_id = str((args.azure_client_id or deploy_kv.get(VarsEnum.AZURE_CLIENT_ID.value)) or "").strip()
    azure_tenant_id = str((deploy_kv.get(VarsEnum.AZURE_TENANT_ID.value)) or "").strip()
    azure_subscription_id = str((deploy_kv.get(VarsEnum.AZURE_SUBSCRIPTION_ID.value)) or "").strip()

    if args.auto_fill_azure_ids:
        info = get_az_account_info()
        if not azure_tenant_id:
            azure_tenant_id = info.get("tenantId") or ""
            if azure_tenant_id:
                print("ℹ️  [info] Filled AZURE_TENANT_ID from az account show")
        if not azure_subscription_id:
            azure_subscription_id = info.get("id") or ""
            if azure_subscription_id:
                print("ℹ️  [info] Filled AZURE_SUBSCRIPTION_ID from az account show")

        azure_app_display_name = (args.azure_app_display_name or "").strip()
        if not azure_client_id and azure_app_display_name:
            azure_client_id = get_app_client_id_by_display_name(azure_app_display_name) or ""
        if not azure_client_id and args.auto_fill_azure_client_id:
            azure_client_id = _az_single_app_client_id() or ""
        
        # Security: Do NOT fall back to picking the first app if multiple exist.
        if not azure_client_id and args.auto_fill_azure_client_id:
            # Check if there are multiple apps to warn the user?
            # _az_single_app_client_id returns None if 0 or >1.
            pass

    missing_required = [
        k
        for k, v in (
            (VarsEnum.AZURE_CLIENT_ID.value, azure_client_id),
            (VarsEnum.AZURE_TENANT_ID.value, azure_tenant_id),
            (VarsEnum.AZURE_SUBSCRIPTION_ID.value, azure_subscription_id),
        )
        if not str(v or "").strip()
    ]
    if missing_required:
        raise SystemExit(
            "[env] Missing Azure OIDC values needed by the deploy workflow: "
            + ", ".join(missing_required)
            + ". Set them in .env.deploy or pass --azure-client-id / --azure-app-display-name."
        )

    if azure_client_id:
        _set_variable(repo=repo, name=VarsEnum.AZURE_CLIENT_ID.value, value=azure_client_id, dry_run=dry_run)
        if args.ensure_federated_credential and not dry_run:
            try:
                explicit_subject = (args.oidc_subject or "").strip()
                subjects: set[str] = set()
                if explicit_subject:
                    subjects.add(explicit_subject)
                else:
                    default_branch = _detect_default_branch(repo) or "main"
                    subjects.add(f"repo:{repo}:ref:refs/heads/{default_branch}")
                    # Also authorize the 'production' environment for GitHub Actions deployment jobs
                    subjects.add(f"repo:{repo}:environment:production")

                    current_branch = _detect_current_branch()
                    if current_branch and current_branch != default_branch:
                        subjects.add(f"repo:{repo}:ref:refs/heads/{current_branch}")

                for subject in sorted(subjects):
                    _ensure_federated_credential(app_id=azure_client_id, repo=repo, subject=subject)
            except Exception as e:
                # Do not block syncing Actions vars/secrets when Azure CLI isn't available/logged in.
                print(f"[warn] Could not ensure Azure federated credential via az CLI: {e}", file=sys.stderr)
    if azure_tenant_id:
        _set_variable(repo=repo, name=VarsEnum.AZURE_TENANT_ID.value, value=azure_tenant_id, dry_run=dry_run)
    if azure_subscription_id:
        _set_variable(repo=repo, name=VarsEnum.AZURE_SUBSCRIPTION_ID.value, value=azure_subscription_id, dry_run=dry_run)

    # 1) Always store runtime dotenv as a secret.
    _set_secret(repo=repo, name=SecretsEnum.RUNTIME_ENV_DOTENV.value, value=runtime_text, dry_run=dry_run)

    # 1b) Also sync specific runtime keys needed by the workflow directly.
    for spec in RUNTIME_SCHEMA:
        val = str(runtime_kv.get(spec.key.value) or "").strip()
        if not val:
            continue
        if EnvTarget.GH_ACTIONS_VAR in spec.targets:
            _set_variable(repo=repo, name=spec.key.value, value=val, dry_run=dry_run)
        if EnvTarget.GH_ACTIONS_SECRET in spec.targets:
            _set_secret(repo=repo, name=spec.key.value, value=val, dry_run=dry_run)

    if args.only_files:
        return

    if not args.also_sync_keys:
        return

    # 2) Optionally sync deploy-time keys, strictly derived from schema targets.
    for spec in DEPLOY_SCHEMA:
        # Already handled above.
        if spec.key.value in REQUIRED_FOR_AZURE_LOGIN:
            continue
        # Runtime dotenv file content is already set above.
        if spec.key == SecretsEnum.RUNTIME_ENV_DOTENV:
            continue

        val = str(deploy_kv.get(spec.key.value) or "").strip()
        if not val:
            continue

        if EnvTarget.GH_ACTIONS_VAR in spec.targets:
            _set_variable(repo=repo, name=spec.key.value, value=val, dry_run=dry_run)
        if EnvTarget.GH_ACTIONS_SECRET in spec.targets:
            _set_secret(repo=repo, name=spec.key.value, value=val, dry_run=dry_run)


if __name__ == "__main__":
    main()
