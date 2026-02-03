#!/usr/bin/env python3
"""Remove empty per-camera nested `data/` directories under incoming/.

Why this exists:
- Some cameras attempt `CWD /data` while logged into a per-camera jailed FTP homedir.
- pyftpdlib interprets that as a path *within* the jail, creating:
    /data/incoming/<camera_id>/data/
  even though uploads later land elsewhere.

Safety:
- Default is dry-run.
- Only deletes a camera's `incoming/<camera_id>/data` directory if it contains no files.
  (Empty directories are OK; any file prevents deletion.)

Usage:
  python3 scripts/cleanup_empty_camera_data_dirs.py --out-dir /data
  python3 scripts/cleanup_empty_camera_data_dirs.py --out-dir /data --apply
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def _has_any_files(root: Path) -> bool:
    try:
        for p in root.rglob("*"):
            # Treat any regular file (including 0-byte) as "non-empty".
            if p.is_file():
                return True
    except FileNotFoundError:
        return False
    return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove empty per-camera nested 'data/' directories under incoming/ (dry-run by default)"
    )
    parser.add_argument("--out-dir", default="/data", help="Root storage directory (default: /data)")
    parser.add_argument("--apply", action="store_true", help="Actually delete directories (default: dry-run)")

    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    incoming_root = out_dir / "incoming"

    if not incoming_root.exists():
        print(f"missing: {incoming_root}")
        return

    scanned = 0
    would_delete = 0
    deleted = 0
    skipped_nonempty = 0
    skipped_missing = 0

    for camera_dir in sorted(incoming_root.iterdir()):
        if not camera_dir.is_dir():
            continue

        scanned += 1
        nested = camera_dir / "data"
        if not nested.exists():
            skipped_missing += 1
            continue

        if _has_any_files(nested):
            skipped_nonempty += 1
            print(f"skip_nonempty: {nested}")
            continue

        would_delete += 1
        if args.apply:
            shutil.rmtree(nested)
            deleted += 1
            print(f"deleted: {nested}")
        else:
            print(f"would_delete: {nested}")

    print(
        "summary: scanned={scanned} would_delete={would_delete} deleted={deleted} skipped_missing={skipped_missing} skipped_nonempty={skipped_nonempty} apply={apply}".format(
            scanned=scanned,
            would_delete=would_delete,
            deleted=deleted,
            skipped_missing=skipped_missing,
            skipped_nonempty=skipped_nonempty,
            apply=bool(args.apply),
        )
    )


if __name__ == "__main__":
    main()
