#!/usr/bin/env python3
"""Delete directories from the Azure Files share backing /data.

Why this exists:
- The repo also includes scripts/azure_storage_cleanup.py which deletes files *inside the container*.
  That requires the ACI container group to be running.
- If the container group is stopped, we can still delete the Azure Files contents directly
  using Azure CLI Storage commands.

Safety:
- Default is dry-run; pass --apply to actually delete.
- Deletes only the directories you specify (e.g. incoming, spool).

It auto-detects the storage account + file share by inspecting the ACI container group's
volume mounts and finding the Azure Files volume mounted at /data.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_dotenv_quiet(path: Path) -> dict[str, str]:
    # Avoid importing python-dotenv here; keep this script dependency-free.
    if not path.exists():
        return {}
    env: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        env.setdefault(k, v)
    return env


def _run(cmd: list[str], *, capture_json: bool = False) -> object:
    try:
        if capture_json:
            out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
            return json.loads(out.decode("utf-8"))
        subprocess.check_call(cmd)
        return None
    except subprocess.CalledProcessError as e:
        msg = (getattr(e, "output", b"") or b"").decode("utf-8", errors="replace").strip()
        if msg:
            raise RuntimeError(msg) from e
        raise


@dataclass(frozen=True)
class ShareMount:
    storage_account: str
    share_name: str


def _detect_data_share(*, resource_group: str, container_group: str) -> ShareMount:
    # We need to map volumeMounts -> volumes -> azureFile shareName/storageAccountName.
    doc = _run(
        [
            "az",
            "container",
            "show",
            "-g",
            resource_group,
            "-n",
            container_group,
            "-o",
            "json",
        ],
        capture_json=True,
    )
    if not isinstance(doc, dict):
        raise RuntimeError("Unexpected az container show output")

    containers = doc.get("containers") or []
    volumes = doc.get("volumes") or []

    # Find the volume mount at /data.
    mount_volume_name: str | None = None
    for c in containers:
        if not isinstance(c, dict):
            continue
        for vm in c.get("volumeMounts") or []:
            if not isinstance(vm, dict):
                continue
            if str(vm.get("mountPath") or "").strip() == "/data":
                mount_volume_name = str(vm.get("name") or "").strip() or None
                break
        if mount_volume_name:
            break

    if not mount_volume_name:
        raise RuntimeError("Could not find a volume mounted at /data in the container group")

    for v in volumes:
        if not isinstance(v, dict):
            continue
        if str(v.get("name") or "").strip() != mount_volume_name:
            continue
        azure_file = v.get("azureFile")
        if not isinstance(azure_file, dict):
            continue
        stg = str(azure_file.get("storageAccountName") or "").strip()
        share = str(azure_file.get("shareName") or "").strip()
        if not stg or not share:
            break
        return ShareMount(storage_account=stg, share_name=share)

    raise RuntimeError("Could not resolve Azure Files share for the /data mount")


def _get_storage_key(*, resource_group: str, storage_account: str) -> str:
    key = subprocess.check_output(
        [
            "az",
            "storage",
            "account",
            "keys",
            "list",
            "-g",
            resource_group,
            "-n",
            storage_account,
            "--query",
            "[0].value",
            "-o",
            "tsv",
        ],
        stderr=subprocess.DEVNULL,
    ).decode("utf-8").strip()
    if not key:
        raise RuntimeError("Failed to fetch storage account key")
    return key


def _az_storage_json(args: list[str], *, storage_account: str, storage_key: str) -> object:
    # Use env vars so we don't have to put the key into argv.
    env = dict(os.environ)
    env["AZURE_STORAGE_ACCOUNT"] = storage_account
    env["AZURE_STORAGE_KEY"] = storage_key
    out = subprocess.check_output(["az", *args, "-o", "json"], env=env)
    return json.loads(out.decode("utf-8"))


def _az_storage_call(args: list[str], *, storage_account: str, storage_key: str) -> None:
    env = dict(os.environ)
    env["AZURE_STORAGE_ACCOUNT"] = storage_account
    env["AZURE_STORAGE_KEY"] = storage_key
    subprocess.check_call(["az", *args], env=env)


def _list_entries(*, share_name: str, path: str, storage_account: str, storage_key: str) -> list[dict]:
    # Returns entries with at least: name, type
    doc = _az_storage_json(
        [
            "storage",
            "file",
            "list",
            "--share-name",
            share_name,
            "--path",
            path,
        ],
        storage_account=storage_account,
        storage_key=storage_key,
    )
    if isinstance(doc, list):
        return [x for x in doc if isinstance(x, dict)]
    return []


def _delete_file(*, share_name: str, file_path: str, storage_account: str, storage_key: str) -> None:
    _az_storage_call(
        [
            "storage",
            "file",
            "delete",
            "--share-name",
            share_name,
            "--path",
            file_path,
            "--output",
            "none",
            "--only-show-errors",
        ],
        storage_account=storage_account,
        storage_key=storage_key,
    )


def _delete_empty_dir(*, share_name: str, dir_path: str, storage_account: str, storage_key: str) -> None:
    _az_storage_call(
        [
            "storage",
            "directory",
            "delete",
            "--share-name",
            share_name,
            "--name",
            dir_path,
            "--output",
            "none",
            "--only-show-errors",
        ],
        storage_account=storage_account,
        storage_key=storage_key,
    )


def _purge_dir(
    *,
    share_name: str,
    dir_path: str,
    storage_account: str,
    storage_key: str,
    apply: bool,
    stats: dict[str, int],
) -> None:
    try:
        entries = _list_entries(
            share_name=share_name,
            path=dir_path,
            storage_account=storage_account,
            storage_key=storage_key,
        )
    except subprocess.CalledProcessError:
        stats["missing_dirs"] += 1
        print(f"missing_dir: {dir_path}")
        return

    for e in entries:
        name = str(e.get("name") or "").strip()
        typ = str(e.get("type") or "").strip()
        if not name:
            continue
        child = f"{dir_path}/{name}" if dir_path else name

        t = typ.strip().lower()

        if t in {"file", "f"}:
            stats["files"] += 1
            if not apply:
                print(f"file: {child}")
                continue
            _delete_file(share_name=share_name, file_path=child, storage_account=storage_account, storage_key=storage_key)
            stats["files_deleted"] += 1
            continue

        if t in {"dir", "directory", "d"}:
            stats["dirs"] += 1
            _purge_dir(
                share_name=share_name,
                dir_path=child,
                storage_account=storage_account,
                storage_key=storage_key,
                apply=apply,
                stats=stats,
            )
            # Best-effort delete after contents.
            if apply:
                try:
                    _delete_empty_dir(
                        share_name=share_name,
                        dir_path=child,
                        storage_account=storage_account,
                        storage_key=storage_key,
                    )
                    stats["dirs_deleted"] += 1
                except subprocess.CalledProcessError:
                    stats["dir_delete_errors"] += 1
            continue

        # Unknown type
        stats["unknown"] += 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Recursively delete Azure Files directories (via Azure CLI)")
    parser.add_argument("--resource-group", "-g", default=None)
    parser.add_argument("--container-group", "-n", default=None)
    parser.add_argument(
        "--storage-account",
        default=None,
        help="Optional storage account name. If set, skips ACI mount auto-detection.",
    )
    parser.add_argument(
        "--share-name",
        default=None,
        help="Optional Azure Files share name. If set, skips ACI mount auto-detection.",
    )
    parser.add_argument("--dirs", nargs="+", required=True, help="Directory names at the share root to purge (e.g. incoming spool)")
    parser.add_argument("--apply", action="store_true", help="Actually delete. Without this flag, runs dry-run and prints files.")

    args = parser.parse_args()

    repo_root = _repo_root()
    deploy_env = _load_dotenv_quiet(repo_root / ".env.deploy")

    rg = (args.resource_group or os.getenv("AZURE_RESOURCE_GROUP") or deploy_env.get("AZURE_RESOURCE_GROUP") or "").strip()
    if not rg:
        raise SystemExit("Missing resource group. Pass --resource-group or set AZURE_RESOURCE_GROUP.")

    storage_account = (args.storage_account or "").strip()
    share_name = (args.share_name or "").strip()

    if storage_account and share_name:
        mount = ShareMount(storage_account=storage_account, share_name=share_name)
    elif storage_account or share_name:
        raise SystemExit("Pass both --storage-account and --share-name, or neither (auto-detect via ACI).")
    else:
        group = (args.container_group or os.getenv("AZURE_CONTAINER_NAME") or deploy_env.get("AZURE_CONTAINER_NAME") or "camera-storage-viewer").strip()
        mount = _detect_data_share(resource_group=rg, container_group=group)

    print(f"Using storage account: {mount.storage_account}")
    print(f"Using file share:     {mount.share_name}")

    key = _get_storage_key(resource_group=rg, storage_account=mount.storage_account)

    stats = {
        "files": 0,
        "dirs": 0,
        "files_deleted": 0,
        "dirs_deleted": 0,
        "missing_dirs": 0,
        "dir_delete_errors": 0,
        "unknown": 0,
    }

    for d in args.dirs:
        d = str(d).strip().lstrip("/")
        if not d:
            continue
        print(f"Purging: {d} (apply={bool(args.apply)})")
        _purge_dir(
            share_name=mount.share_name,
            dir_path=d,
            storage_account=mount.storage_account,
            storage_key=key,
            apply=bool(args.apply),
            stats=stats,
        )
        if args.apply:
            try:
                _delete_empty_dir(
                    share_name=mount.share_name,
                    dir_path=d,
                    storage_account=mount.storage_account,
                    storage_key=key,
                )
                stats["dirs_deleted"] += 1
            except subprocess.CalledProcessError:
                stats["dir_delete_errors"] += 1

    print(
        "summary:" + " ".join(
            [
                f"files={stats['files']}",
                f"dirs={stats['dirs']}",
                f"files_deleted={stats['files_deleted']}",
                f"dirs_deleted={stats['dirs_deleted']}",
                f"missing_dirs={stats['missing_dirs']}",
                f"dir_delete_errors={stats['dir_delete_errors']}",
                f"unknown={stats['unknown']}",
            ]
        )
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130)
