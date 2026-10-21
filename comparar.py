#!/usr/bin/env python3
"""Compare ALT sidecars against reference ALTs.

Per pair, prints three blocks:
  1. REF vs LOCAL texts
  2. Judge model's opinion (raw, as-is — no parsing, no gating)
  3. Deterministic measurements (chars, length_ok, truncated, words)

The judgment is the user's. This tool does not compute success.
Config is strict: missing general.judge.model fails hard.
"""
import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime

from altlib import (
    BASE_DIR, load_config, manifest, sha256_file,
)
from altlib.contract import text_sha256
from altlib.llm import llm_generate
from altlib.paths import (
    JUDGE_CACHE_PATH, PAIRS_FILE, REFERENCIAS_PATH, resolve_dir,
)


_CFG = load_config()
JUDGE_MODEL = _CFG["general"]["judge"]["model"]
LOCAL_CHAR_LIMIT = _CFG["general"]["max_alt_chars"]


JUDGE_PROMPT = """REF (describes an image): {ref}

LOCAL (also describes an image): {local}

Do these two texts describe the same image or scene? Answer in one short sentence."""

COMPARISON_FIELDS = (
    "timestamp", "image_id", "image_path", "source_url",
    "local_kind", "local_model",
    "ref_chars", "local_chars", "delta_chars",
    "length_ok", "truncated",
    "ref_words", "local_words",
    "judge_model", "judge_opinion",
)


def file_hash(s):
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def is_truncated(text):
    s = text.rstrip()
    return s.endswith("…") or s.endswith("...")


def deterministic(local, ref):
    return {
        "ref_chars": len(ref),
        "local_chars": len(local),
        "delta_chars": len(local) - len(ref),
        "length_ok": len(local) <= LOCAL_CHAR_LIMIT,
        "truncated": is_truncated(local),
        "ref_words": len(ref.split()),
        "local_words": len(local.split()),
    }


def read_sidecar(image_path):
    path = image_path + ".alt.txt"
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    if "\n---\n" not in raw:
        return None
    alt_part, meta_part = raw.split("\n---\n", 1)
    meta = {}
    for line in meta_part.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return alt_part.strip(), meta


def validate_manifest(image_path, alt_text, meta):
    expected = meta.get("image_sha256", "").strip()
    if not expected:
        return False, "sidecar has no image_sha256 (regenerate with alt.py)"
    if expected != sha256_file(image_path):
        return False, "image_sha256 mismatch (image modified)"
    expected_alt = meta.get("alt_sha256", "").strip()
    if not expected_alt:
        return False, "sidecar has no alt_sha256 (regenerate with alt.py)"
    if expected_alt != text_sha256(alt_text):
        return False, "alt_sha256 mismatch (ALT manually edited)"
    return True, "ok"


def collect_downloads_pairs():
    if not os.path.exists(PAIRS_FILE):
        return []
    with open(PAIRS_FILE, encoding="utf-8") as fh:
        raw = json.load(fh)
    out = []
    for p in raw:
        lf = p.get("local_file")
        if not lf or not os.path.isfile(lf):
            continue
        out.append({
            "image_id": file_hash(p["url"]),
            "image_path": lf,
            "reference_alt": p["reference_alt"],
            "source_url": p.get("url", ""),
        })
    return out


def collect_examples_pairs():
    if not os.path.exists(REFERENCIAS_PATH):
        return []
    with open(REFERENCIAS_PATH, encoding="utf-8") as fh:
        refs = json.load(fh)
    examples_dir = resolve_dir(load_config()["general"]["examples_dir"])
    out = []
    for name, ref_alt in refs.items():
        path = str(examples_dir / name)
        if not os.path.isfile(path):
            continue
        out.append({
            "image_id": file_hash(name),
            "image_path": path,
            "reference_alt": ref_alt,
            "source_url": "",
        })
    return out


def load_cache(path=JUDGE_CACHE_PATH):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def save_cache(cache, path=JUDGE_CACHE_PATH):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=2)


def judge_opinion(ref, local, cache):
    key = "op:" + hashlib.sha1(
        (ref + "\x00" + local).encode("utf-8")).hexdigest()[:16]
    if key in cache and isinstance(cache[key], str):
        cached = cache[key]
        if not cached.startswith("(judge error:"):
            return cached, True
    try:
        raw, _metrics = llm_generate(
            model=JUDGE_MODEL,
            prompt=JUDGE_PROMPT.format(ref=ref, local=local),
            keep_alive="10m",
            num_predict=200,
            num_ctx=2048,
            temperature=0.0,
        )
        cache[key] = raw
        return raw, False
    except Exception as exc:
        return f"(judge error: {exc})", False


def print_pair(idx, total, pair, local, meta, opinion, cached):
    sep = "=" * 72
    ref = pair["reference_alt"]
    d = deterministic(local, ref)
    print()
    print(sep)
    print(f"[{idx}/{total}] {pair['image_id']}   kind={meta.get('kind','?')}")
    print(f"image:   {pair['image_path']}")
    print(sep)
    print(f"REF   (A) {d['ref_chars']:>3}c: {ref}")
    print(f"LOCAL (B) {d['local_chars']:>3}c: {local}")
    print(sep)
    print(f"judge ({JUDGE_MODEL}):")
    for line in opinion.splitlines() or [""]:
        print(f"  | {line}")
    print(sep)
    print("deterministic:")
    print(f"  ref_chars:     {d['ref_chars']}")
    print(f"  local_chars:   {d['local_chars']}")
    print(f"  delta_chars:   {d['delta_chars']:+d}")
    print(f"  length_ok:     {'yes' if d['length_ok'] else 'NO'}  (<= {LOCAL_CHAR_LIMIT})")
    print(f"  truncated:     {'YES' if d['truncated'] else 'no'}")
    print(f"  ref_words:     {d['ref_words']}")
    print(f"  local_words:   {d['local_words']}")
    if cached:
        print("  (judge opinion cached)")
    print(sep)


def write_row(pair, local, meta, opinion, csv_path):
    ref = pair["reference_alt"]
    d = deterministic(local, ref)
    row = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "image_id": pair["image_id"],
        "image_path": pair["image_path"],
        "source_url": pair["source_url"],
        "local_kind": meta.get("kind", "?"),
        "local_model": meta.get("model", "?"),
        "ref_chars": d["ref_chars"],
        "local_chars": d["local_chars"],
        "delta_chars": d["delta_chars"],
        "length_ok": int(d["length_ok"]),
        "truncated": int(d["truncated"]),
        "ref_words": d["ref_words"],
        "local_words": d["local_words"],
        "judge_model": JUDGE_MODEL,
        "judge_opinion": opinion.replace("\n", " | "),
    }
    with open(csv_path, "a", newline="", encoding="utf-8") as fh:
        csv.DictWriter(fh, fieldnames=COMPARISON_FIELDS,
                       extrasaction="ignore").writerow(row)


def ensure_csv_header(path):
    if os.path.exists(path):
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.DictWriter(fh, fieldnames=COMPARISON_FIELDS).writeheader()


def main():
    warmup = subprocess.run(
        ["python3", "runtime.py", "ensure", "qwen"],
        cwd=str(BASE_DIR),
    )
    if warmup.returncode != 0:
        sys.exit("judge disabled (ALT_NO_QWEN=1 or general.llms.qwen=false)")

    p = argparse.ArgumentParser(
        description="sidecar vs reference ALT comparison "
                    "(default: downloads/, --examples: exemplos/, --all: both)")
    p.add_argument("--examples", action="store_true",
                   help="compare exemplos/ against referencias.json")
    p.add_argument("--all", action="store_true",
                   help="compare both downloads/ and exemplos/")
    p.add_argument("--csv", default=str(resolve_dir(_CFG["general"]["comparison_csv"])))
    p.add_argument("--quiet", action="store_true",
                   help="only print one line per pair")
    p.add_argument("--limit", type=int, default=0)
    args = p.parse_args()

    if args.examples and args.all:
        sys.exit("--examples and --all are mutually exclusive")

    if args.all:
        pairs = collect_downloads_pairs() + collect_examples_pairs()
        which = "downloads/ + exemplos/"
    elif args.examples:
        pairs = collect_examples_pairs()
        which = "exemplos/"
    else:
        pairs = collect_downloads_pairs()
        which = "downloads/ (via coletados.json)"
    if not pairs:
        sys.exit(f"no pairs found in {which}")
    if args.limit:
        pairs = pairs[:args.limit]

    ready, problems = [], []
    for pair in pairs:
        sidecar = read_sidecar(pair["image_path"])
        if sidecar is None:
            problems.append((pair, "no sidecar"))
            continue
        alt, meta = sidecar
        ok, reason = validate_manifest(pair["image_path"], alt, meta)
        if not ok:
            problems.append((pair, reason))
            continue
        ready.append((pair, alt, meta))

    if problems:
        print(f"\n{len(problems)} pair(s) need alt.py first:", file=sys.stderr)
        for pair, reason in problems:
            print(f"  {pair['image_path']}", file=sys.stderr)
            print(f"    reason: {reason}", file=sys.stderr)
            print(f"    fix:    python3 alt.py {pair['image_path']}",
                  file=sys.stderr)
        print(file=sys.stderr)

    if not ready:
        print("nothing to compare — run the commands above first", file=sys.stderr)
        sys.exit(1)

    ensure_csv_header(args.csv)
    cache = load_cache()
    print(f"{len(ready)} pair(s) ready, {len(problems)} need attention",
          file=sys.stderr)
    print(f"judge: {JUDGE_MODEL}", file=sys.stderr)

    for i, (pair, alt, meta) in enumerate(ready, 1):
        opinion, cached = judge_opinion(pair["reference_alt"], alt, cache)
        if not args.quiet:
            print_pair(i, len(ready), pair, alt, meta, opinion, cached)
        else:
            d = deterministic(alt, pair["reference_alt"])
            print(f"[{i}/{len(ready)}] {pair['image_id']}  "
                  f"len={d['local_chars']}c "
                  f"({'ok' if d['length_ok'] else 'OVER'}) "
                  f"{'truncated' if d['truncated'] else ''}")
        write_row(pair, alt, meta, opinion, args.csv)

    save_cache(cache)
    source = "all" if args.all else ("exemplos" if args.examples else "downloads")
    manifest.update(
        "last_compare",
        source=source,
        pairs=len(ready),
        problems=len(problems),
        judge=JUDGE_MODEL,
    )
    print(f"\nDone: {len(ready)} printed, CSV → {args.csv}", file=sys.stderr)


if __name__ == "__main__":
    main()