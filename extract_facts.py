"""Objective facts via OpenDecision.

One call per image: visual_type (choice) + action (choice).
Both questions in English (SigLIP2 is trained in English).
Formatting in Portuguese with confidence for the Ollama prompt.
"""
import argparse
import base64
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
from altlib import contract, load_config
from altlib.paths import (
    ACOES_PATH, VOCAB_PATH,
    OD_PORT, OD_URL, OD_OBS_JSONL,
)


def _load_vocab():
    return json.loads(VOCAB_PATH.read_text())


def _load_actions():
    return json.loads(ACOES_PATH.read_text())


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _log_observation(image_path, type_ans):
    """Append one line per OD call: the full distribution for later analysis."""
    try:
        OD_OBS_JSONL.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "image": Path(image_path).name,
            "image_sha256": _sha256(image_path),
            "choice": type_ans.get("choice", ""),
            "probabilities": type_ans.get("probabilities", {}),
            "confidence": type_ans.get("confidence", 0.0),
        }
        with open(OD_OBS_JSONL, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


_RUNTIME = Path(__file__).resolve().parent / "runtime.py"


def ensure_server() -> None:
    subprocess.run(
        ["python3", str(_RUNTIME), "ensure", "od"],
        check=True,
        cwd=str(_RUNTIME.parent),
    )


def stop_server() -> None:
    subprocess.run(
        ["pkill", "-f", f"opendecision serve --port {OD_PORT}"],
        check=False,
    )


def _ask(image_b64: str, questions: dict, retry: int = 1) -> dict:
    last_error = None
    for attempt in range(retry + 1):
        try:
            r = httpx.post(
                f"{OD_URL}/v1/systemone",
                json={"state": {"type": "image", "data": image_b64},
                      "questions": questions},
                timeout=120.0,
            )
            if r.status_code >= 500:
                raise RuntimeError(f"server 5xx: {r.status_code}")
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last_error = exc
            if attempt < retry:
                print(f"warning: {exc}; restarting server", file=sys.stderr)
                stop_server()
                time.sleep(1.5)
                ensure_server()
    raise last_error


def _format_choice(ans: dict) -> str:
    choice = ans.get("choice")
    conf = ans.get("confidence")
    probs = ans.get("probabilities", {})
    if not choice:
        return "unknown"
    if conf is None or conf >= 0.6 or not probs:
        return f"{choice} (conf {conf:.2f})" if conf else str(choice)
    top = sorted(probs.items(), key=lambda kv: -kv[1])[:2]
    second = ", ".join(f"{k}={v:.2f}" for k, v in top[1:])
    return f"{choice} (uncertain; 2nd: {second})"


def _round1(image_b64, vocab, actions, debug=False):
    questions = {
        "visual_type": {
            "type": "choice",
            "instructions": "What kind of visual content is this?",
            "criteria": vocab["tipo_visual"],
        },
        "action": {
            "type": "choice",
            "instructions": "What action is happening in this scene?",
            "criteria": actions,
        },
    }
    raw = _ask(image_b64, questions)
    if debug:
        print("=== R1 (visual_type + action) ===", file=sys.stderr)
        print(json.dumps(raw, indent=2, ensure_ascii=False), file=sys.stderr)
    answers = raw.get("answers") or {}
    return answers.get("visual_type", {}), answers.get("action", {})


def extract_facts(image_path, debug=False, force_restart=False):
    if force_restart:
        stop_server()
        time.sleep(1.0)
    ensure_server()
    model = load_config()["general"]["classifier_model"]
    vocab = _load_vocab()
    actions = _load_actions()
    print(f"  [od] model: {model}", file=sys.stderr, flush=True)
    with open(image_path, "rb") as fh:
        image_b64 = base64.b64encode(fh.read()).decode()

    type_ans, action_ans = _round1(image_b64, vocab, actions, debug=debug)
    _log_observation(image_path, type_ans)
    contract.write_od(image_path, type_ans, action_ans, model)

    return {
        "visual_type": _format_choice(type_ans),
        "_kind_raw": type_ans.get("choice", ""),
        "_kind_conf": type_ans.get("probabilities", {}).get(
            type_ans.get("choice", ""),
            type_ans.get("confidence", 0.0),
        ),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("image", nargs="?")
    p.add_argument("--debug", action="store_true")
    p.add_argument("--stop-server", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.stop_server:
        stop_server()
        return
    if not args.image:
        sys.exit("image required (or use --stop-server)")

    facts = extract_facts(args.image, debug=args.debug)
    if args.json:
        print(json.dumps(facts, ensure_ascii=False, indent=2))
    else:
        for k, v in facts.items():
            print(f"{k}: {v}")


if __name__ == "__main__":
    main()
