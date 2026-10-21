#!/usr/bin/env python3
"""Extract (image, reference ALT) pairs from a Gajim history.txt export.

OPTIONAL — validation tooling.

This script is a template for gathering (image, reference ALT) pairs
from chat logs. Use it when you want to validate your local pipeline
against a "gold standard" produced by a high-capacity API bot
(GPT-4V, Claude, Gemini, etc.).

The marker regex lives in config.json under general.gajim_alt_marker.
Adapt it for your bot's format. If it doesn't match, collect.py
produces zero pairs and warns.

Output: coletados.json (contains chat data — never commit).
Used by: comparar.py (default mode reads coletados.json).
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime

from altlib import load_config
from altlib.paths import PAIRS_FILE, resolve_dir


MESSAGE_START = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) (.+?): (.*)$")
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
IMAGE_EXT = re.compile(r"\.(png|jpe?g|webp|bmp|gif)(?:\?\S*)?$", re.IGNORECASE)
HEX_HASH = re.compile(r"\b([a-f0-9]{40}|[a-f0-9]{64})\b")

DEFAULT_WINDOW = 600


def _compile_alt_marker(cfg):
    return re.compile(cfg["general"]["gajim_alt_marker"], re.IGNORECASE)


def parse_messages(path):
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    messages = []
    current = None
    for line in lines:
        match = MESSAGE_START.match(line)
        if match:
            if current is not None:
                messages.append(current)
            date, time_, author, text = match.groups()
            current = {
                "timestamp": datetime.strptime(f"{date} {time_}", "%Y-%m-%d %H:%M:%S"),
                "author": author.strip(),
                "text": text,
            }
        elif current is not None:
            current["text"] += "\n" + line
    if current is not None:
        messages.append(current)
    return messages


def collect_image_refs(messages):
    refs = []
    for msg in messages:
        for url in URL_RE.findall(msg["text"]):
            if IMAGE_EXT.search(url) or "/media/" in url or "/attachments/" in url:
                refs.append({"timestamp": msg["timestamp"],
                             "author": msg["author"], "url": url})
        for sha in HEX_HASH.findall(msg["text"]):
            refs.append({"timestamp": msg["timestamp"],
                         "author": msg["author"], "hash": sha})
    return refs


def collect_alt_entries(messages, alt_marker):
    entries = []
    for msg in messages:
        match = alt_marker.search(msg["text"])
        if not match:
            continue
        entries.append({
            "timestamp": msg["timestamp"],
            "image_author": match.group(1),
            "time_hint": (int(match.group(2)), int(match.group(3))),
            "alt_text": match.group(4).strip(),
        })
    return entries


def index_downloads(downloads_dir):
    files = []
    if not os.path.isdir(downloads_dir):
        return files
    for name in os.listdir(downloads_dir):
        path = os.path.join(downloads_dir, name)
        if not os.path.isfile(path) or not IMAGE_EXT.search(name):
            continue
        files.append({
            "path": path,
            "name": name,
            "hashes": HEX_HASH.findall(name),
            "mtime": os.path.getmtime(path),
        })
    return files


def match_by_hash(ref, files):
    if "hash" not in ref:
        return None
    for f in files:
        if ref["hash"] in f["hashes"]:
            return f["path"]
    return None


def match_by_url(url, files):
    basename = url.rsplit("/", 1)[-1].split("?")[0]
    if not basename:
        return None
    stem, ext = os.path.splitext(basename)
    ext = ext.lower()
    if not stem:
        return None

    for f in files:
        fstem, fext = os.path.splitext(f["name"])
        if fext.lower() != ext:
            continue
        if fstem == stem or fstem.startswith(stem + "_"):
            return f["path"]

    for f in files:
        if stem in f["name"]:
            return f["path"]

    for hashfn in (hashlib.sha1, hashlib.sha256):
        url_hash = hashfn(url.encode()).hexdigest()
        for f in files:
            if url_hash in f["hashes"]:
                return f["path"]
    return None


def match_by_mtime(ts_epoch, files, window, used):
    best, best_delta = None, window + 1
    for f in files:
        if f["path"] in used:
            continue
        delta = abs(f["mtime"] - ts_epoch)
        if delta < best_delta and delta <= window:
            best, best_delta = f["path"], delta
    return best


def _pick_ref(entry, image_refs):
    """Return the most likely image ref for this alt entry, or None.

    Prefers same-author refs; within those, prefers minute-match against
    the HH:MM hint (the history logs server time, the marker logs local
    time, so only the minute is reliable). Falls back to newest.
    """
    same_author = sorted(
        [r for r in image_refs
         if r["timestamp"] <= entry["timestamp"]
         and r["author"] == entry["image_author"]],
        key=lambda r: r["timestamp"], reverse=True,
    )
    other = sorted(
        [r for r in image_refs
         if r["timestamp"] <= entry["timestamp"]
         and r["author"] != entry["image_author"]],
        key=lambda r: r["timestamp"], reverse=True,
    )

    pool = same_author or other
    if not pool:
        return None

    hint_minute = entry["time_hint"][1] if entry.get("time_hint") else None
    if hint_minute is not None:
        for ref in pool:
            if ref["timestamp"].minute == hint_minute:
                return ref
    return pool[0]


def build_pairs(messages, downloads_dir, alt_marker,
                window=DEFAULT_WINDOW, debug=False):
    entries = collect_alt_entries(messages, alt_marker)
    image_refs = collect_image_refs(messages)
    files = index_downloads(downloads_dir)

    if debug:
        print(f"alt entries: {len(entries)}", file=sys.stderr)
        print(f"image refs:  {len(image_refs)}", file=sys.stderr)
        print(f"files:       {len(files)}", file=sys.stderr)

    used = set()
    pairs = []
    for entry in entries:
        ref = _pick_ref(entry, image_refs)
        if ref is None:
            if debug:
                print(f"  NO REF at {entry['timestamp']}: "
                      f"{entry['alt_text'][:50]}...", file=sys.stderr)
            continue

        url = ref.get("url")
        image_time = ref["timestamp"]

        local_file = None
        source = None
        if "hash" in ref:
            local_file = match_by_hash(ref, files)
            if local_file:
                source = "hash"
        if local_file is None and url:
            local_file = match_by_url(url, files)
            if local_file:
                source = "url"
        if local_file is None:
            local_file = match_by_mtime(image_time.timestamp(), files, window, used)
            if local_file:
                source = "mtime"

        if local_file:
            used.add(local_file)

        if local_file is None and url is None:
            if debug:
                print(f"  NO MATCH at {entry['timestamp']}: "
                      f"{entry['alt_text'][:50]}...", file=sys.stderr)
            continue

        delay = (entry["timestamp"] - image_time).total_seconds()
        pairs.append({
            "local_file": local_file,
            "url": url,
            "source": source or "url_only",
            "reference_alt": entry["alt_text"],
            "image_time": image_time.isoformat(),
            "alt_time": entry["timestamp"].isoformat(),
            "delay_seconds": delay,
            "uncertain": delay > window,
        })

        if debug:
            display = local_file if local_file else (url or "(none)")
            print(f"  MATCH ({source or 'url_only'}): {display} -> "
                  f"{entry['alt_text'][:50]}...", file=sys.stderr)

    return pairs


def main():
    parser = argparse.ArgumentParser(
        description="extract (image, reference ALT) pairs from Gajim history")
    parser.add_argument("history", help="path to history.txt")
    parser.add_argument("--output", default=str(PAIRS_FILE),
                        help="output JSON path (default: coletados.json)")
    parser.add_argument("--downloads", default=None,
                        help="override downloads directory (default: general.downloads_dir)")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW,
                        help="seconds tolerance for mtime matching (default: 600)")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    cfg = load_config()
    alt_marker = _compile_alt_marker(cfg)
    downloads_dir = args.downloads or resolve_dir(cfg["general"]["downloads_dir"])

    messages = parse_messages(args.history)
    print(f"messages: {len(messages)}", file=sys.stderr)

    pairs = build_pairs(messages, downloads_dir, alt_marker,
                        window=args.window, debug=args.debug)

    if not pairs:
        print("warning: 0 pairs found. The alt marker regex likely does not "
              "match your bot. Adjust general.gajim_alt_marker in config.json.",
              file=sys.stderr)

    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(pairs, fh, ensure_ascii=False, indent=2)

    with_local = sum(1 for p in pairs if p["local_file"])
    print(f"pairs: {len(pairs)} -> {args.output} "
          f"(with local file: {with_local})", file=sys.stderr)
    uncertain = sum(1 for p in pairs if p["uncertain"])
    if uncertain:
        print(f"warning: {uncertain} with delay > {args.window}s", file=sys.stderr)


if __name__ == "__main__":
    main()
