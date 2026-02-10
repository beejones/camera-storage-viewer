from __future__ import annotations

import argparse
from pathlib import Path

from src.ingest import ingest_incoming_once


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest uploads: move stable files from incoming/ into videos/ library")
    parser.add_argument("--out-dir", default="/data", help="Root storage directory (default: /data)")
    parser.add_argument(
        "--min-age-seconds",
        type=int,
        default=60,
        help="Minimum age (mtime) before a file is considered complete (default: 60)",
    )
    parser.add_argument("--apply", action="store_true", help="Actually move files (default: dry-run)")

    args = parser.parse_args()

    result = ingest_incoming_once(out_dir=Path(args.out_dir), min_age_seconds=args.min_age_seconds, apply=bool(args.apply))
    print(
        "summary:scanned={scanned} moved={moved} skipped_recent={skipped_recent} skipped_nonvideo={skipped_nonvideo} skipped_existing={skipped_existing} apply={apply}".format(
            scanned=result.scanned,
            moved=result.moved,
            skipped_recent=result.skipped_recent,
            skipped_nonvideo=result.skipped_nonvideo,
            skipped_existing=result.skipped_existing,
            apply=bool(args.apply),
        )
    )


if __name__ == "__main__":
    main()
