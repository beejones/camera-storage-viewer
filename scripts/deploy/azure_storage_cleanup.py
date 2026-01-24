#!/usr/bin/env python3
"""Cleanup persisted data on Azure Files mounted at /data in ACI.

This script deletes files *in the Azure Files share* by executing a Python snippet
inside the running ACI container (so it works even if typical Linux tools like
`find` are not available in the image).

Safety features:
- Requires a target under /data (default: /data/incoming)
- Supports --dry-run (default) and requires --apply to actually delete
- Supports --all or --before-date (ISO-8601 date/time)

Examples:
  # Dry-run: show what would be deleted before 2026-01-01
  python scripts/deploy/azure_storage_cleanup.py --before-date 2026-01-01

  # Delete everything under /data/incoming
  python scripts/deploy/azure_storage_cleanup.py --all --apply

  # Target a subfolder (e.g. per-camera)
  python scripts/deploy/azure_storage_cleanup.py --path /data/incoming/front --before-date 2026-01-01 --apply
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

from env_schema import VarsEnum


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


# Add scripts dir to path to allow importing azure_utils
sys.path.append(str(Path(__file__).parent))
try:
    from azure_utils import run_az_command
except ImportError:
    sys.path.append("scripts")
    from azure_utils import run_az_command


def _load_env(env_file: str | None) -> None:
    repo_root = _repo_root()

    # Load runtime .env first (doesn't matter much here, but keeps behavior consistent)
    runtime = repo_root / ".env"
    if runtime.exists():
        load_dotenv(runtime, override=False)

    if env_file:
        load_dotenv(Path(env_file), override=True)
        return

    deploy = repo_root / ".env.deploy"
    if deploy.exists():
        load_dotenv(deploy, override=True)


def _require_under_data(path: str) -> str:
    p = (path or "").strip()
    if not p:
        raise SystemExit("Missing --path")

    # Normalize: ensure leading slash.
    if not p.startswith("/"):
        p = "/" + p

    if p == "/data":
        return p

    if not p.startswith("/data/"):
        raise SystemExit(
            f"Refusing to operate on path outside /data: {p}. "
            "Pass a path under /data (e.g. /data/incoming)."
        )

    return p


def _python_cleanup_snippet(*, root_path: str, all_files: bool, before_date: str | None, apply: bool, delete_empty_dirs: bool) -> str:
    cutoff = (before_date or "").strip()
    mode = "all" if all_files else "before"
    apply_int = 1 if apply else 0
    del_empty_int = 1 if delete_empty_dirs else 0

    # This code is passed to `python -c` inside `az container exec`.
    # We quote the full snippet with shlex.quote(), so it is safe even if it contains
    # characters like quotes or newlines.
    return (
        "import os,sys,datetime; "
        f"root={json.dumps(root_path)}; "
        f"mode={json.dumps(mode)}; "
        f"cutoff={json.dumps(cutoff)}; "
        f"apply={apply_int}; "
        f"delete_empty={del_empty_int}; "
        "if not os.path.isabs(root):\n    print(\"error: path must be absolute\", file=sys.stderr); sys.exit(2); "
        "if root != \"/data\" and not root.startswith(\"/data/\"):\n    print(\"error: refusing to operate outside /data\", file=sys.stderr); sys.exit(2); "
        "if mode == \"before\":\n"
        "    if not cutoff:\n        print(\"error: missing cutoff\", file=sys.stderr); sys.exit(2);\n"
        "    try:\n"
        "        # Accept YYYY-MM-DD or full ISO; treat naive as local time.\n"
        "        dt=datetime.datetime.fromisoformat(cutoff.replace(\"Z\",\"+00:00\"))\n"
        "    except Exception as e:\n"
        "        print(\"error: invalid --before-date (use YYYY-MM-DD or ISO): \" + str(e), file=sys.stderr); sys.exit(2)\n"
        "    cutoff_ts=dt.timestamp()\n"
        "else:\n"
        "    cutoff_ts=None\n"
        "deleted=0; kept=0; errors=0; seen=0;\n"
        "for dirpath, dirnames, filenames in os.walk(root):\n"
        "    for fn in filenames:\n"
        "        full=os.path.join(dirpath, fn)\n"
        "        try:\n"
        "            st=os.stat(full)\n"
        "        except FileNotFoundError:\n"
        "            continue\n"
        "        except Exception as e:\n"
        "            print(\"warn: stat failed: \" + full + \" :: \" + str(e), file=sys.stderr); errors+=1; continue\n"
        "        seen+=1\n"
        "        should_del = True if mode==\"all\" else (st.st_mtime < cutoff_ts)\n"
        "        if not should_del:\n"
        "            kept+=1; continue\n"
        "        if not apply:\n"
        "            print(full)\n"
        "            deleted+=1\n"
        "            continue\n"
        "        try:\n"
        "            os.remove(full)\n"
        "            deleted+=1\n"
        "        except Exception as e:\n"
        "            print(\"warn: delete failed: \" + full + \" :: \" + str(e), file=sys.stderr); errors+=1\n"
        "if apply and delete_empty:\n"
        "    # Remove empty dirs bottom-up (skip root itself).\n"
        "    for dirpath, dirnames, filenames in os.walk(root, topdown=False):\n"
        "        if dirpath == root:\n"
        "            continue\n"
        "        try:\n"
        "            if not os.listdir(dirpath):\n"
        "                os.rmdir(dirpath)\n"
        "        except Exception:\n"
        "            pass\n"
        "print(f\"summary: scanned={seen} matched={deleted} kept={kept} errors={errors} apply={bool(apply)}\", file=sys.stderr)\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete files from Azure Files mounted at /data in ACI")

    parser.add_argument(
        "--env-file",
        default=None,
        help="Optional env file to load (defaults to repo root .env.deploy if present)",
    )

    parser.add_argument("--resource-group", "-g", default=None)
    parser.add_argument("--container-group", "-n", default=None, help="ACI container group name (default: AZURE_CONTAINER_NAME or camera-storage-viewer)")
    parser.add_argument("--container-name", default=None, help="Container name within the group (default: same as --container-group)")

    parser.add_argument("--path", default="/data/incoming", help="Path inside container to clean (must be under /data)")

    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--all", action="store_true", help="Delete all files under --path")
    scope.add_argument("--before-date", default=None, help="Delete files strictly older than this ISO date/time (e.g. 2026-01-01)")

    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete files. Without this flag, the script runs in dry-run mode and prints matched files.",
    )
    parser.add_argument(
        "--delete-empty-dirs",
        action="store_true",
        help="After deleting files, remove empty directories under --path (best-effort).",
    )

    args = parser.parse_args()

    _load_env(args.env_file)

    rg = (args.resource_group or os.getenv(VarsEnum.AZURE_RESOURCE_GROUP.value) or "").strip()
    if not rg:
        raise SystemExit("Missing Azure resource group. Pass --resource-group or set AZURE_RESOURCE_GROUP.")

    group = (
        args.container_group
        or os.getenv(VarsEnum.AZURE_CONTAINER_NAME.value)
        or "camera-storage-viewer"
    ).strip() or "camera-storage-viewer"
    container = (args.container_name or group).strip() or group

    target_path = _require_under_data(args.path)

    snippet = _python_cleanup_snippet(
        root_path=target_path,
        all_files=bool(args.all),
        before_date=str(args.before_date or "").strip() or None,
        apply=bool(args.apply),
        delete_empty_dirs=bool(args.delete_empty_dirs),
    )

    exec_cmd = f"python -c {shlex.quote(snippet)}"

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[cleanup] mode={mode} rg={rg} group={group} container={container} path={target_path}")
    if args.before_date:
        print(f"[cleanup] before_date={args.before_date}")

    try:
        out = run_az_command(
            [
                "container",
                "exec",
                "--resource-group",
                rg,
                "--name",
                group,
                "--container-name",
                container,
                "--exec-command",
                exec_cmd,
                "--output",
                "tsv",
            ],
            capture_output=True,
            ignore_errors=False,
            verbose=True,
        )
    except subprocess.CalledProcessError as e:
        err = (getattr(e, "stderr", None) or "").strip()
        if "ResourceNotFound" in err and "containerGroups" in err:
            existing = run_az_command(
                [
                    "container",
                    "list",
                    "--resource-group",
                    rg,
                    "--query",
                    "[].name",
                    "-o",
                    "tsv",
                ],
                capture_output=True,
                ignore_errors=True,
                verbose=False,
            )

            names = []
            if isinstance(existing, str):
                names = [n.strip() for n in existing.splitlines() if n.strip()]

            hint = "\n".join(
                [
                    "[cleanup] ACI container group not found.",
                    f"[cleanup] Tried: --container-group {group}",
                    ("[cleanup] Container groups in this resource group:\n" + "\n".join([f"- {n}" for n in names]))
                    if names
                    else "[cleanup] No container groups found in this resource group.",
                    "",
                    "Try rerunning with the right group name, e.g.:",
                    f"  python scripts/deploy/azure_storage_cleanup.py --resource-group {rg} --container-group <NAME> --all --apply",
                ]
            )
            raise SystemExit(hint)

        raise

    # In dry-run mode, the snippet prints the matched files to STDOUT.
    if isinstance(out, str) and out.strip():
        print(out)


if __name__ == "__main__":
    main()
