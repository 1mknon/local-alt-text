#!/usr/bin/env python3
"""Analyze logs/od_observacoes.jsonl: distributions, unmapped classes, thresholds."""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from altlib import load_config, relpath
from altlib.paths import OD_OBS_JSONL
from altlib.routing import TYPE_TO_ROUTE


BUCKETS = [
    (0.90, ">= 0.90"),
    (0.70, "0.70-0.89"),
    (0.60, "0.60-0.69"),
    (0.45, "0.45-0.59"),
    (0.30, "0.30-0.44"),
    (0.00, "< 0.30"),
]
THRESHOLD_PROBES = (0.30, 0.40, 0.45, 0.50, 0.60, 0.70)


def load_records(path):
    if not path.is_file():
        sys.exit(f"log not found: {path}\n"
                 f"run `python3 alt.py --batch exemplos/` first to populate it")
    out = []
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"skipping line {i}: {exc}", file=sys.stderr)
    return out


def dedupe_latest(records):
    by_sha = {}
    for r in records:
        sha = r.get("image_sha256", "")
        if not sha:
            continue
        prev = by_sha.get(sha)
        if prev is None or r.get("ts", "") > prev.get("ts", ""):
            by_sha[sha] = r
    return by_sha


def analyze_record(r, od_conf_min):
    probs = r.get("probabilities", {})
    choice = r.get("choice", "")
    if not probs or choice not in probs:
        return None

    prob = probs[choice]
    second_key, second_prob = "", 0.0
    for k, v in probs.items():
        if k != choice and v > second_prob:
            second_key, second_prob = k, v

    mapped = choice in TYPE_TO_ROUTE
    route = TYPE_TO_ROUTE.get(choice)
    would_override = mapped and prob >= od_conf_min

    if not mapped:
        status = "UNMAPPED"
    elif would_override:
        status = "OVERRIDE"
    else:
        status = "KEPT"

    return {
        "choice": choice,
        "prob": prob,
        "second_key": second_key,
        "second_prob": second_prob,
        "mapped": mapped,
        "route": route,
        "would_override": would_override,
        "status": status,
    }


def prob_bucket(prob):
    for floor, label in BUCKETS:
        if prob >= floor:
            return label
    return BUCKETS[-1][1]


def main():
    p = argparse.ArgumentParser(description="Analyze OD observations log")
    p.add_argument("--log", default=str(OD_OBS_JSONL))
    p.add_argument("--limit", type=int, default=0,
                   help="max images to list individually (0 = all)")
    p.add_argument("--json", action="store_true",
                   help="machine-readable output")
    args = p.parse_args()

    cfg = load_config()
    od_conf_min = cfg["general"]["od_conf_min"]

    records = load_records(Path(args.log))
    if not records:
        sys.exit(f"log is empty: {args.log}")
    unique = dedupe_latest(records)
    if not unique:
        sys.exit("no records with image_sha256 found")

    analyzed = []
    for sha, r in unique.items():
        result = analyze_record(r, od_conf_min)
        if result is None:
            continue
        result["image"] = r.get("image", "?")
        result["image_sha256"] = sha
        analyzed.append(result)
    analyzed.sort(key=lambda a: a["image"])

    if args.json:
        print(json.dumps({
            "log": str(args.log),
            "od_conf_min": od_conf_min,
            "records_total": len(records),
            "images_unique": len(analyzed),
            "images": analyzed,
        }, ensure_ascii=False, indent=2))
        return

    print("=" * 78)
    print("OD OBSERVATIONS ANALYSIS")
    print("=" * 78)
    print(f"log:          {relpath(args.log)}")
    print(f"od_conf_min:  {od_conf_min}")
    print(f"route map:    {len(TYPE_TO_ROUTE)} entries (from altlib.routing)")
    print(f"records:      {len(records)} total")
    print(f"unique imgs:  {len(analyzed)} (by sha256, latest per image)")
    print()

    print("=" * 78)
    print("PER IMAGE")
    print("=" * 78)
    shown = analyzed[:args.limit] if args.limit else analyzed
    for i, a in enumerate(shown, 1):
        print()
        print(f"[{i}] {a['image']}  ({a['image_sha256'][:8]})")
        print(f"    choice: {a['choice']:<14} ({a['prob']:.2f})   "
              f"second: {a['second_key']:<14} ({a['second_prob']:.2f})")
        if a["status"] == "UNMAPPED":
            print(f"    -> UNMAPPED (add \"{a['choice']}\" to TYPE_TO_ROUTE)")
        elif a["status"] == "OVERRIDE":
            print(f"    -> {a['route']:<14} [OVERRIDE]")
        else:
            print(f"    -> {a['route']:<14} [KEPT, prob < {od_conf_min}]")
    if args.limit and len(analyzed) > args.limit:
        print(f"\n... {len(analyzed) - args.limit} more not shown (use --limit 0)")

    print()
    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)

    by_choice = Counter(a["choice"] for a in analyzed)
    print("\nby choice:")
    for choice, n in by_choice.most_common():
        pct = 100 * n / len(analyzed)
        print(f"  {choice:<22} {n:>3}  ({pct:>5.1f}%)")

    by_route = Counter(a["route"] for a in analyzed if a["route"])
    if by_route:
        print("\nby route (mapped only):")
        for route, n in by_route.most_common():
            print(f"  {route:<22} {n:>3}")

    by_status = Counter(a["status"] for a in analyzed)
    print(f"\ndecisions at od_conf_min = {od_conf_min}:")
    for status in ("OVERRIDE", "KEPT", "UNMAPPED"):
        print(f"  {status:<10} {by_status.get(status, 0):>3}")

    print("\ntop-probability buckets:")
    by_bucket = Counter(prob_bucket(a["prob"]) for a in analyzed)
    for _, label in BUCKETS:
        n = by_bucket.get(label, 0)
        if n:
            pct = 100 * n / len(analyzed)
            print(f"  {label:<12} {n:>3}  ({pct:>5.1f}%)")

    print("\nthreshold sensitivity (overrides at different od_conf_min):")
    for threshold in THRESHOLD_PROBES:
        count = sum(1 for a in analyzed
                    if a["mapped"] and a["prob"] >= threshold)
        marker = "  <- current" if abs(threshold - od_conf_min) < 1e-6 else ""
        print(f"  {threshold:.2f}  ->  {count:>3} overrides{marker}")

    unmapped = [a for a in analyzed if a["status"] == "UNMAPPED"]
    if unmapped:
        print("\nunmapped classes (need mapping in TYPE_TO_ROUTE):")
        for cls, n in Counter(a["choice"] for a in unmapped).most_common():
            print(f"  {cls:<22} {n:>3}")
            for a in unmapped:
                if a["choice"] == cls:
                    print(f"      {a['image']}  (prob {a['prob']:.2f})")


if __name__ == "__main__":
    main()