#!/usr/bin/env python3
"""Generate alt-text. Orchestrates: heuristics → OD → ollama.

Each stage is a subprocess with a clear contract. No bypass imports.
Config is strict; missing keys fail hard.

Batch runs in two phases:
  Phase 1: classification (heuristics + OD for ambiguous images)
  Phase 2: generation (ollama 7b loaded once, no swaps)

Single-image does the same for one image.
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from altlib import (
    BASE_DIR, contract,
    encode_image, ensure_ollama, load_config, log_event, llm_generate,
    log_stage, manifest, relpath, resize_for_model, routes_with_suffix,
    sha256_file, system_status, system_status_text, unload_ollama_model,
)
from altlib.paths import resolve_dir
from altlib.routing import TYPE_TO_ROUTE


ALT_GUARDRAILS = (
    "Sobre textos, letras, números, siglas e logomarcas: só transcreva quando "
    "tiver muita certeza de que leu corretamente. Nunca adivinhe, complete ou "
    "corrija palavras, e não presuma marcas ou nomes conhecidos. Se estiver em "
    "dúvida sobre um texto, não o cite: descreva apenas o elemento (por exemplo, "
    "'um logotipo' ou 'texto ilegível') sem inventar o conteúdo. "
    "Nunca identifique pessoas pelo nome. Não use markdown."
)
ALT_SYSTEM = (
    "Você descreve imagens para fins de acessibilidade, em português do Brasil. "
    + ALT_GUARDRAILS
)


METRICS_FIELDS = (
    "timestamp", "file", "classifier", "kind", "type_confidence", "reason",
    "heur_seconds", "od_seconds", "resize_seconds", "generate_seconds",
    "total_seconds",
    "model", "ctx", "resized", "original_dims", "final_dims",
    "prompt_tokens", "generated", "generation_tokens_per_s",
    "vram_used_mb", "alt_preview",
)


def _py(script, *args, timeout=300):
    proc = subprocess.run(
        ["python3", script, *args],
        capture_output=True, text=True, timeout=timeout,
        cwd=str(BASE_DIR),
    )
    if proc.stderr:
        print(proc.stderr.rstrip(), file=sys.stderr, flush=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{script} exit={proc.returncode}: {(proc.stderr or '')[-400:]}")
    return proc.stdout


def call_heuristics(image_path):
    return json.loads(_py("extract_heuristics.py", image_path, "--route", "--json", timeout=30))


def call_od(image_path):
    return json.loads(_py("extract_facts.py", image_path, "--json", timeout=300))


def stop_od():
    subprocess.run(
        ["python3", "extract_facts.py", "--stop-server"],
        capture_output=True, timeout=10, cwd=str(BASE_DIR),
    )


def _apply_od_routing(result, od_data, cfg, source):
    visual = od_data.get("visual_type", {})
    od_kind = visual.get("choice", "")
    probs = visual.get("probabilities", {})
    od_conf = probs.get(od_kind, 0.0)
    od_conf_min = cfg["general"]["od_conf_min"]

    if od_kind not in TYPE_TO_ROUTE:
        print(f"  [od] ({source}) unmapped '{od_kind}' (p={od_conf:.2f})",
              file=sys.stderr)
    else:
        route = TYPE_TO_ROUTE[od_kind]
        if od_conf < od_conf_min:
            print(f"  [od] ({source}) kept {result['label']} "
                  f"(said {od_kind}, p={od_conf:.2f} < {od_conf_min})",
                  file=sys.stderr)
        else:
            print(f"  [od] ({source}) -> {route} (p={od_conf:.2f})",
                  file=sys.stderr)
            result["label"] = route
            result["confidence"] = 0.85
            result["classifier"] = "opendecision"

    action = od_data.get("action") or {}
    action_choice = action.get("choice", "")
    action_prob = action.get("probabilities", {}).get(action_choice, 0.0)
    action_conf_min = cfg["general"]["action_conf_min"]
    action_prompt_min = cfg["general"]["action_prompt_min"]

    if action_choice and action_choice != "none" and action_prob >= action_conf_min:
        result["facts"]["action"] = action_choice
        if action_prob < action_prompt_min:
            result["facts"]["_action_weak"] = True
            print(f"  [od] ({source}) action: {action_choice} (p={action_prob:.2f}, weak - contract only)",
                  file=sys.stderr)
        else:
            print(f"  [od] ({source}) action: {action_choice} (p={action_prob:.2f})",
                  file=sys.stderr)


def classify(image_path, cfg, use_od):
    timings = {}
    t0 = time.time()
    heur = call_heuristics(image_path)
    timings["heuristics"] = round(time.time() - t0, 3)
    log_stage("heuristics", timings["heuristics"], file=image_path,
              label=heur["label"], decidable=heur["decidable"])

    result = {
        "label": heur["label"],
        "confidence": heur["confidence"],
        "classifier": "heuristic",
        "reason": heur["reason"],
        "heur_text": heur.get("heur_text", ""),
        "facts": {},
        "timings": timings,
    }
    timings["opendecision"] = 0.0

    if heur["decidable"] or not use_od:
        return result

    t0 = time.time()
    od_data = contract.read_od(image_path)
    source = "contract"

    if od_data is None:
        source = "inline"
        loaded = system_status().get("ollama_model")
        if loaded:
            print(f"  [od] freeing VRAM ({loaded})...", file=sys.stderr, flush=True)
            unload_ollama_model(loaded)
        print(f"  [od] ambiguous ({heur['reason']})...", file=sys.stderr, flush=True)
        try:
            call_od(image_path)
            od_data = contract.read_od(image_path)
        except Exception as exc:
            print(f"  [od] failed: {exc}", file=sys.stderr)

    if od_data is not None:
        _apply_od_routing(result, od_data, cfg, source)

    timings["opendecision"] = round(time.time() - t0, 3)
    log_stage("opendecision", timings["opendecision"], file=image_path)
    return result


def build_prompt(base_prompt, heur_text, facts):
    parts = [base_prompt]
    if heur_text:
        parts.append("\n\nHeurísticas objetivas (determinísticas):\n" + heur_text)
    facts_block = "\n".join(f"- {k}: {v}" for k, v in facts.items()
                             if not k.startswith("_"))
    if facts_block:
        parts.append("\n\nTags de modelo de visão:\n" + facts_block)
    if len(parts) > 1:
        parts.append(
            "\n\nUse esse contexto como delimitação. Se algo contradisser "
            "o que você vê na imagem, priorize a imagem. "
            "Não invente detalhes ausentes."
        )
    return "".join(parts)


def truncate_alt(text, max_chars):
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    for sep in (". ", "! ", "? ", ".\n", "!\n", "?\n"):
        pos = window.rfind(sep)
        if pos > max_chars * 0.5:
            return window[:pos + 1].strip()
    parts = window.rsplit(" ", 1)
    head = parts[0] if len(parts) > 1 else window
    return head.rstrip(".,;:") + "…"


def _resize(image_path, route, timings):
    t0 = time.time()
    path, was_resized, orig, final = resize_for_model(image_path, route["max_side"])
    timings["resize"] = round(time.time() - t0, 3)
    log_stage("resize", timings["resize"], file=image_path, resized=was_resized)
    return {"path": path, "was_resized": was_resized, "orig": orig, "final": final}


def _generate(resized_path, route, prompt, cfg, timings, image_path):
    ensure_ollama()
    t0 = time.time()
    alt_text, metrics = llm_generate(
        model=route["model"], prompt=prompt,
        images=[encode_image(resized_path)],
        system=ALT_SYSTEM,
        keep_alive=route["keep_alive"],
        num_predict=route["num_predict"],
        num_ctx=cfg["general"]["ctx"],
    )
    timings["generate"] = round(time.time() - t0, 3)
    log_stage("generate", timings["generate"], file=image_path, **metrics)
    return alt_text, metrics


def _persist(image_path, alt_text, classification, route, timings, metrics,
             resized, cfg, args):
    save_sidecar(image_path, alt_text, {
        "classifier": classification["classifier"],
        "kind": classification["label"],
        "confidence": round(classification["confidence"], 3),
        "model": route["model"],
        "image_sha256": sha256_file(image_path),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    })
    if not args.no_metrics:
        append_metrics({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "file": os.path.basename(image_path),
            "classifier": classification["classifier"],
            "kind": classification["label"],
            "type_confidence": round(classification["confidence"], 3),
            "reason": classification["reason"],
            "heur_seconds": timings.get("heuristics", 0),
            "od_seconds": timings.get("opendecision", 0),
            "resize_seconds": timings.get("resize", 0),
            "generate_seconds": timings.get("generate", 0),
            "total_seconds": timings.get("total", 0),
            "model": route["model"],
            "ctx": cfg["general"]["ctx"],
            "resized": resized["was_resized"],
            "original_dims": f"{resized['orig'][0]}x{resized['orig'][1]}",
            "final_dims": f"{resized['final'][0]}x{resized['final'][1]}",
            "prompt_tokens": metrics.get("prompt_tokens", 0),
            "generated": metrics.get("generated_tokens", 0),
            "generation_tokens_per_s": metrics.get("generation_tokens_per_s", 0),
            "vram_used_mb": metrics.get("vram_used_mb", ""),
            "alt_preview": alt_text[:80].replace("\n", " "),
        }, resolve_dir(cfg["general"]["metrics_csv"]))
    log_event({"event": "alt_text", "file": os.path.basename(image_path),
               "classifier": classification["classifier"],
               "kind": classification["label"],
               "confidence": round(classification["confidence"], 3),
               "alt": alt_text, "timings": timings, "metrics": metrics})


def generate_and_save(image_path, classification, routes, cfg, args):
    label = classification["label"]
    if label not in routes:
        raise SystemExit(f"route '{label}' not declared. available: {sorted(routes)}")
    route = routes[label]
    prompt = build_prompt(route["prompt"],
                          classification["heur_text"], classification["facts"])
    timings = dict(classification["timings"])
    t_start = time.time()

    resized = _resize(image_path, route, timings)
    try:
        alt_text, metrics = _generate(resized["path"], route, prompt,
                                       cfg, timings, image_path)
    finally:
        if resized["path"] != image_path and os.path.exists(resized["path"]):
            os.unlink(resized["path"])

    alt_text = truncate_alt(alt_text, cfg["general"]["max_alt_chars"])
    timings["total"] = round(time.time() - t_start, 3)

    _persist(image_path, alt_text, classification, route, timings, metrics,
             resized, cfg, args)

    return {
        "file": os.path.basename(image_path),
        "alt_text": alt_text,
        "label": label,
        "classifier": classification["classifier"],
        "confidence": classification["confidence"],
        "reason": classification["reason"],
        "timings": timings,
        "metrics": metrics,
    }


def save_sidecar(image_path, alt_text, meta):
    from altlib.contract import text_sha256
    full = {"alt_sha256": text_sha256(alt_text), **meta}
    with open(str(image_path) + ".alt.txt", "w", encoding="utf-8") as fh:
        fh.write(alt_text + "\n\n---\n")
        for k, v in full.items():
            fh.write(f"{k}: {v}\n")


def append_metrics(row, path):
    path = Path(path)
    new_file = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=METRICS_FIELDS, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def print_profile(timings):
    print(f"\n{'='*60}\nSTAGE TIMINGS\n{'='*60}", file=sys.stderr)
    for stage, seconds in timings.items():
        print(f"  {stage:<20} {seconds:>7.2f}s", file=sys.stderr)


def mode_single(image_path, cfg, routes, args):
    classification = classify(image_path, cfg, use_od=not args.no_od)
    stop_od()
    result = generate_and_save(image_path, classification, routes, cfg, args)
    print(result["alt_text"])
    if args.profile:
        print_profile(result["timings"])
    else:
        print(f"\n[{result['label']} | {result['classifier']} | "
              f"{result['timings']['total']:.1f}s]", file=sys.stderr)
    return result


def mode_batch(directory, cfg, routes, args):
    extensions = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    directory = Path(directory).absolute()
    all_images = [p for p in directory.iterdir()
                  if p.is_file() and p.suffix.lower() in extensions
                  and not p.name.endswith(".alt.txt")]
    if not all_images:
        sys.exit(f"no images in {relpath(directory)}")
    images = sorted(p for p in all_images
                    if args.force or not Path(str(p) + ".alt.txt").exists())
    if not images:
        print(f"all {len(all_images)} images already processed in {relpath(directory)}",
              file=sys.stderr)
        print("use --force to reprocess", file=sys.stderr)
        sys.exit(0)

    use_od = not args.no_od
    print(f"batch:   {relpath(directory)}")
    print(f"images:  {len(images)}")
    print(f"mode:    {'with OD' if use_od else 'heuristics only'}")
    print("=" * 70)

    t_start = time.time()

    print("\nPhase 1/2: classification", file=sys.stderr)
    classifications = {}
    for i, image in enumerate(images, 1):
        print(f"[{i}/{len(images)}] {image.name}", file=sys.stderr)
        classifications[image] = classify(str(image), cfg, use_od=use_od)

    stop_od()
    time.sleep(1)

    print("\nPhase 2/2: generation", file=sys.stderr)
    successful = 0
    total_chars = 0
    for i, image in enumerate(images, 1):
        try:
            result = generate_and_save(str(image), classifications[image],
                                       routes, cfg, args)
            successful += 1
            total_chars += len(result["alt_text"])
            print(f"\n[{i}/{len(images)}] {image.name}  "
                  f"({result['label']}, {result['timings']['total']:.1f}s)")
            print(f"  {result['alt_text']}")
        except Exception as exc:
            print(f"\n[{i}/{len(images)}] {image.name}  FAILED: {exc}")

    elapsed = time.time() - t_start
    print("\n" + "=" * 70)
    print(f"{successful}/{len(images)} ok | {elapsed:.0f}s | "
          f"{elapsed/len(images):.1f}s/image | "
          f"mean {total_chars/max(successful,1):.0f} chars")
    if successful == 0:
        sys.exit(1)
    if successful < len(images):
        sys.exit(2)

    manifest.update(
        "last_batch",
        dir=relpath(str(directory)),
        images=len(images),
        successful=successful,
        model=sorted({r["model"] for r in routes.values()}),
        elapsed_s=round(elapsed, 1),
    )


def parse_args():
    p = argparse.ArgumentParser(description="alt-text via Ollama")
    p.add_argument("image", nargs="?", default=None)
    p.add_argument("--batch", default=None, metavar="DIR")
    p.add_argument("--no-od", action="store_true",
                   help="skip OpenDecision (heuristics only, faster for debugging)")
    p.add_argument("--profile", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-metrics", dest="no_metrics", action="store_true",
                   help="don't write to metricas.csv")
    p.add_argument("--force", action="store_true",
                   help="process even if sidecar exists")
    return p.parse_args()


def main():
    args = parse_args()
    cfg = load_config()
    routes = routes_with_suffix(cfg)

    if args.dry_run:
        ts = datetime.now().isoformat(timespec="seconds")
        print(f"ts: {ts}")
        print(f"status: {system_status_text()}")
        print(f"config: {len(routes)} routes")
        print(f"routes: {sorted(routes)}")
        print(f"model: {routes[list(routes)[0]]['model']}")
        m = manifest.load()
        for key in ("last_batch", "last_compare", "last_od_probe"):
            if key in m:
                print(f"{key}: {m[key]}")
        return

    if args.batch:
        mode_batch(args.batch, cfg, routes, args)
        return

    if not args.image:
        sys.exit("image required (or --batch / --dry-run)")

    image_path = str(Path(args.image).absolute())
    if not Path(image_path).is_file():
        sys.exit(f"file not found: {image_path}")

    mode_single(image_path, cfg, routes, args)


if __name__ == "__main__":
    main()