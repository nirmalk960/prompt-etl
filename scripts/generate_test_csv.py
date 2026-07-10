#!/usr/bin/env python3
"""Generate synthetic CSV for smoke / load tests.

Usage:
  python generate_test_csv.py --target-mb 250 --out /tmp/heavy_250mb.csv
  python generate_test_csv.py --rows 5000000 --out /tmp/heavy_rows.csv
"""

from __future__ import annotations

import argparse
import os
import sys

HEADER = "id,name,department,salary,region,notes\n"
ROW_TEMPLATE = (
    "{id},{name},{dept},{salary},{region},{notes}\n"
)
DEPTS = ("Engineering", "Sales", "Marketing", "Support", "Finance")
REGIONS = ("US", "EU", "APAC", "LATAM")


def write_rows(fp, start_id: int, count: int) -> int:
    written = 0
    for i in range(count):
        rid = start_id + i
        dept = DEPTS[rid % len(DEPTS)]
        region = REGIONS[rid % len(REGIONS)]
        salary = 45000 + (rid % 120) * 1000
        notes = f"note_{rid % 97}"
        fp.write(
            ROW_TEMPLATE.format(
                id=rid,
                name=f"User{rid}",
                dept=dept,
                salary=salary,
                region=region,
                notes=notes,
            )
        )
        written += 1
    return written


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True, help="Output CSV path")
    p.add_argument("--target-mb", type=float, default=0, help="Approximate size in MiB")
    p.add_argument("--rows", type=int, default=0, help="Exact row count (overrides --target-mb)")
    p.add_argument("--chunk-rows", type=int, default=100_000, help="Rows per write batch")
    args = p.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)

    if args.rows <= 0 and args.target_mb <= 0:
        print("Specify --target-mb or --rows", file=sys.stderr)
        return 1

    with open(args.out, "w", encoding="utf-8") as fp:
        fp.write(HEADER)
        if args.rows > 0:
            total = 0
            remaining = args.rows
            rid = 1
            while remaining > 0:
                batch = min(args.chunk_rows, remaining)
                total += write_rows(fp, rid, batch)
                rid += batch
                remaining -= batch
                fp.flush()
            print(f"wrote {total:,} rows -> {args.out} ({os.path.getsize(args.out):,} bytes)")
            return 0

        target_bytes = int(args.target_mb * 1024 * 1024)
        rid = 1
        total = 0
        while fp.tell() < target_bytes:
            batch = args.chunk_rows
            total += write_rows(fp, rid, batch)
            rid += batch
            if total % 500_000 == 0:
                fp.flush()
                print(f"  ... {fp.tell() / (1024*1024):.1f} MiB", file=sys.stderr)
        print(f"wrote ~{total:,} rows -> {args.out} ({os.path.getsize(args.out):,} bytes)")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
