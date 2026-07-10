#!/usr/bin/env python3
"""Generate synthetic newline-delimited JSON (JSONL) for smoke / load tests.

Mirrors the orders_2025.csv schema so existing aggregate/sort/select prompts
work unchanged. DuckDB's read_json_auto (used by the ingest service) handles
NDJSON natively.

Usage:
  python generate_test_json.py --target-gb 10 --out /tmp/orders_10gb.json
  python generate_test_json.py --rows 5000000 --out /tmp/orders_rows.json
"""

from __future__ import annotations

import argparse
import os
import sys

STATUSES = ("pending", "completed", "cancelled", "shipped", "refunded")
REGIONS = ("US", "EU", "APAC", "LATAM")
DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)

ROW_TEMPLATE = (
    '{{"order_id":"ORD{rid:09d}","customer_id":"C{cid:06d}",'
    '"order_status":"{status}","order_date":"{date}",'
    '"order_total":{total},"discount_amount":{discount},"region":"{region}"}}\n'
)


def make_batch(start_id: int, count: int) -> str:
    lines: list[str] = []
    for i in range(count):
        rid = start_id + i
        month = (rid // 1000) % 12
        day = rid % DAYS_IN_MONTH[month] + 1
        total = 40 + (rid % 1200) * 0.85
        lines.append(
            ROW_TEMPLATE.format(
                rid=rid,
                cid=rid % 500_000 + 1,
                status=STATUSES[rid % len(STATUSES)],
                date=f"2025-{month + 1:02d}-{day:02d}",
                total=f"{total:.2f}",
                discount=f"{(rid % 40) * 0.25:.2f}",
                region=REGIONS[rid % len(REGIONS)],
            )
        )
    return "".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True, help="Output JSONL path")
    p.add_argument("--target-gb", type=float, default=0, help="Approximate size in GiB")
    p.add_argument("--rows", type=int, default=0, help="Exact row count (overrides --target-gb)")
    p.add_argument("--chunk-rows", type=int, default=200_000, help="Rows per write batch")
    args = p.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)

    if args.rows <= 0 and args.target_gb <= 0:
        print("Specify --target-gb or --rows", file=sys.stderr)
        return 1

    with open(args.out, "w", encoding="utf-8") as fp:
        rid = 1
        total = 0
        if args.rows > 0:
            remaining = args.rows
            while remaining > 0:
                batch = min(args.chunk_rows, remaining)
                fp.write(make_batch(rid, batch))
                rid += batch
                total += batch
                remaining -= batch
        else:
            target_bytes = int(args.target_gb * 1024**3)
            while fp.tell() < target_bytes:
                fp.write(make_batch(rid, args.chunk_rows))
                rid += args.chunk_rows
                total += args.chunk_rows
                if total % 2_000_000 == 0:
                    print(f"  ... {fp.tell() / 1024**3:.2f} GiB", file=sys.stderr)

    size = os.path.getsize(args.out)
    print(f"wrote {total:,} rows -> {args.out} ({size / 1024**3:.2f} GiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
