#!/usr/bin/env python3
"""Remove OD contracts and/or ALTs to force reprocessing.

Default: downloads/ (real data). --examples or --all to include
the calibration set.
"""
import argparse
import json
import sys
from pathlib import Path

from altlib import load_config, relpath, sha256_file
from altlib.paths import resolve_dir


def find_images(subdirs):
    extensions = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    images = []
    for root in subdirs:
        if not root.is_dir():
            continue
        images.extend(p for p in sorted(root.iterdir())
                      if p.is_file() and p.suffix.lower() in extensions)
    return images


def contract_state(img):
    od = Path(str(img) + ".od.json")
    if not od.is_file():
        return "no-od"
    try:
        data = json.loads(od.read_text())
    except Exception:
        return "od-broken"
    if data.get("image_sha256") != sha256_file(str(img)):
        return "od-stale"
    if "visual_type" not in data:
        return "od-stale"
    return "od"


def has_alt(img):
    return Path(str(img) + ".alt.txt").is_file()


def remove(files):
    removed = 0
    for f in files:
        if f.is_file():
            f.unlink()
            removed += 1
    return removed


def main():
    p = argparse.ArgumentParser(description="Remove contracts and/or ALTs")
    p.add_argument("--examples", action="store_true",
                   help="include exemplos/")
    p.add_argument("--all", action="store_true",
                   help="include downloads/ and exemplos/")
    args = p.parse_args()

    cfg = load_config()
    downloads = resolve_dir(cfg["general"]["downloads_dir"])
    exemplos = resolve_dir(cfg["general"]["examples_dir"])

    if args.all:
        subdirs = (downloads, exemplos)
    elif args.examples:
        subdirs = (exemplos,)
    else:
        subdirs = (downloads,)

    images = find_images(subdirs)
    if not images:
        sys.exit(f"no images in {', '.join(relpath(s) for s in subdirs)}/")

    print(f"{len(images)} image(s) in {', '.join(relpath(s) for s in subdirs)}/:\n")

    rows = []
    for i, img in enumerate(images, 1):
        od = contract_state(img)
        alt = has_alt(img)
        rows.append({"i": i, "img": img, "od": od, "alt": alt,
                     "sha": sha256_file(str(img))[:8]})
        od_tag = {"od": "od", "od-stale": "od-stale",
                  "od-broken": "od-broken", "no-od": "--"}[od]
        alt_tag = "alt" if alt else "--"
        print(f"  {i:>3}. {img.name:<55} {rows[-1]['sha']}  {od_tag:<9} {alt_tag}")

    with_od = [r for r in rows if r["od"] != "no-od"]
    with_alt = [r for r in rows if r["alt"]]
    if not with_od and not with_alt:
        print("\nnothing to clean.")
        return

    print()
    print(f"  [o] remove {len(with_od)} contract(s) OD")
    print(f"  [a] remove {len(with_alt)} ALT(s)")
    print(f"  [b] remove both")
    print(f"  [n] choose by number (remove contract AND alt of chosen)")
    print(f"  [q] quit")

    choice = input("\nchoice [o/a/b/n/q]: ").strip().lower()

    if choice == "o":
        if input(f"remove {len(with_od)} contract(s)? [y/N] ").strip().lower() == "y":
            files = [Path(str(r["img"]) + ".od.json") for r in with_od]
            n = remove(files)
            print(f"{n} contract(s) removed.")
    elif choice == "a":
        files = [Path(str(r["img"]) + ".alt.txt") for r in with_alt]
        n = remove(files)
        print(f"{n} ALT(s) removed.")
    elif choice == "b":
        files = ([Path(str(r["img"]) + ".od.json") for r in with_od]
                 + [Path(str(r["img"]) + ".alt.txt") for r in with_alt])
        n = remove(files)
        print(f"{n} file(s) removed.")
    elif choice == "n":
        raw = input("numbers (e.g. 4,5 or 4-5): ").strip()
        targets = set()
        for part in raw.replace(" ", ",").split(","):
            if not part:
                continue
            if "-" in part:
                try:
                    a, b = part.split("-", 1)
                    targets.update(range(int(a), int(b) + 1))
                except ValueError:
                    pass
            else:
                try:
                    targets.add(int(part))
                except ValueError:
                    pass
        if not targets:
            sys.exit("no valid number.")
        files = []
        for i in sorted(targets):
            r = next((x for x in rows if x["i"] == i), None)
            if not r:
                continue
            if r["od"] != "no-od":
                files.append(Path(str(r["img"]) + ".od.json"))
            if r["alt"]:
                files.append(Path(str(r["img"]) + ".alt.txt"))
        n = remove(files)
        print(f"{n} file(s) removed.")
    else:
        sys.exit("exiting.")


if __name__ == "__main__":
    main()