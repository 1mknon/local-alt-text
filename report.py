#!/usr/bin/env python3
"""Summarize metrics CSV.

Usage: report.py [alt|compare|all] [--alt-csv PATH] [--cmp-csv PATH]
"""
import argparse
import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from altlib import load_config, resolve_dir


def load_rows(path):
    path = Path(path)
    if not path.exists():
        sys.exit(f"CSV not found: {path}")
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def mean(values, default=0.0):
    values = [v for v in values if v is not None]
    return statistics.mean(values) if values else default


def report_alt(path):
    rows = load_rows(path)
    if not rows:
        sys.exit(f"CSV empty: {path}")
    print("=" * 70)
    print(f"ALT METRICS - {path}")
    print("=" * 70)
    print(f"  images processed: {len(rows)}")
    kinds = defaultdict(int)
    for row in rows:
        kinds[row.get("kind", "?")] += 1
    print(f"\n  by kind:")
    for kind, count in sorted(kinds.items()):
        print(f"    {kind:<20} {count:>3}")
    print(f"\n  timing (seconds):")
    for stage in ("heur_seconds", "resize_seconds", "generate_seconds",
                  "total_seconds"):
        values = [to_float(r.get(stage)) for r in rows if r.get(stage)]
        if values:
            print(f"    {stage:<20} mean={mean(values):>7.2f}  "
                  f"max={max(values):>7.2f}")


def report_compare(path, max_alt_chars):
    rows = load_rows(path)
    if not rows:
        sys.exit(f"CSV empty: {path}")
    n = len(rows)
    print("=" * 70)
    print(f"COMPARISON METRICS - {path}")
    print("=" * 70)
    print(f"  pairs: {n}")

    def count(field, values, labels=None):
        c = defaultdict(int)
        for r in rows:
            c[r.get(field, "?")] += 1
        for v in values:
            if c.get(v):
                pct = 100 * c[v] / n
                label = labels.get(v, v) if labels else v
                print(f"    {label:>10}: {c[v]:>3}  ({pct:>5.1f}%)")

    print(f"\n  length_ok (local_chars <= {max_alt_chars}):")
    count("length_ok", ("1", "0"), {"1": "ok", "0": "OVER"})
    print(f"\n  truncated (ends with … or ...):")
    count("truncated", ("1", "0"), {"1": "YES", "0": "no"})

    print(f"\n  char lengths:")
    ref_chars = [to_float(r.get("ref_chars")) for r in rows]
    loc_chars = [to_float(r.get("local_chars")) for r in rows]
    delta_chars = [to_float(r.get("delta_chars")) for r in rows]
    print(f"    REF    mean={mean(ref_chars):.0f}  max={max(ref_chars):.0f}")
    print(f"    LOCAL  mean={mean(loc_chars):.0f}  max={max(loc_chars):.0f}")
    print(f"    DELTA  mean={mean(delta_chars):+.0f}")

    print(f"\n  word counts:")
    ref_words = [to_float(r.get("ref_words")) for r in rows]
    loc_words = [to_float(r.get("local_words")) for r in rows]
    print(f"    REF    mean={mean(ref_words):.1f}")
    print(f"    LOCAL  mean={mean(loc_words):.1f}")

    print(f"\n  by local kind:")
    groups = defaultdict(list)
    for row in rows:
        groups[row.get("local_kind", "?")].append(row)
    for kind, items in sorted(groups.items()):
        lok = sum(1 for r in items if r.get("length_ok") == "1")
        tr = sum(1 for r in items if r.get("truncated") == "1")
        print(f"    {kind:<12} n={len(items):>2}  "
              f"length_ok={lok}/{len(items)}  truncated={tr}/{len(items)}")


def main():
    cfg = load_config()
    alt_default = str(resolve_dir(cfg["general"]["metrics_csv"]))
    cmp_default = str(resolve_dir(cfg["general"]["comparison_csv"]))
    max_alt_chars = cfg["general"]["max_alt_chars"]

    parser = argparse.ArgumentParser(description="Summarize metrics CSV")
    parser.add_argument("mode", choices=("alt", "compare", "all"), nargs="?",
                        default="all")
    parser.add_argument("--alt-csv", default=alt_default)
    parser.add_argument("--cmp-csv", default=cmp_default)
    args = parser.parse_args()

    if args.mode in ("alt", "all"):
        report_alt(args.alt_csv)
    if args.mode == "all":
        print()
    if args.mode in ("compare", "all"):
        report_compare(args.cmp_csv, max_alt_chars)


if __name__ == "__main__":
    main()