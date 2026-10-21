"""Centralized runtime: LLM activation, VRAM-safe transitions, process lifecycle.

Python API:
    from runtime import LLMControl
    ctl = LLMControl(cfg, args)
    if ctl.qwen_enabled:
        ctl.ensure_qwen("qwen2.5vl:7b")
    if ctl.od_enabled:
        ctl.ensure_od()

Subprocess CLI:
    python3 runtime.py status
    python3 runtime.py check qwen
    python3 runtime.py check od
    python3 runtime.py ensure qwen
    python3 runtime.py ensure od
    python3 runtime.py stop

Activation priority (for each LLM):
    env ALT_NO_OD=1 / ALT_NO_QWEN=1  >  CLI --no-od / --no-qwen
    >  config general.llms.{od,qwen}
"""
import argparse
import json
from datetime import datetime
import os
import subprocess
import sys
import time

import httpx

from altlib import (
    load_config,
    ensure_ollama, system_status, unload_ollama_model, manifest,
)
from altlib.paths import OD_BIN, OD_PORT, OD_URL, OD_STARTUP_TIMEOUT, OD_LOG


def _resolve_flag(env_var, cli_flag, cfg_value):
    env = os.environ.get(env_var, "").strip().lower()
    if env in ("1", "true", "yes"):
        return False
    if cli_flag:
        return False
    return bool(cfg_value)


class LLMControl:
    def __init__(self, cfg=None, args=None):
        self.cfg = cfg or load_config()
        llms = self.cfg["general"]["llms"]
        self.od_enabled = _resolve_flag(
            "ALT_NO_OD",
            getattr(args, "no_od", False) if args else False,
            llms["od"],
        )
        self.qwen_enabled = _resolve_flag(
            "ALT_NO_QWEN",
            getattr(args, "no_qwen", False) if args else False,
            llms["qwen"],
        )

    def ensure_qwen(self, model, keep_alive="60m"):
        """Stop OD, ensure Ollama running, warm up model."""
        stop_od()
        ensure_ollama()
        time.sleep(0.5)
        import ollama
        ollama.generate(model=model, prompt="warmup", keep_alive=keep_alive,
                       options={"num_predict": 1})

    def ensure_od(self):
        """Free Qwen VRAM, start OD server, wait for ready."""
        loaded = system_status().get("ollama_model")
        if loaded:
            print(f"  [runtime] freeing VRAM ({loaded})...",
                  file=sys.stderr, flush=True)
            unload_ollama_model(loaded)
            time.sleep(1.5)
        if _od_alive():
            return
        _start_od()
        _wait_od()

    def status(self):
        s = system_status()
        m = manifest.load()
        return {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "od_enabled": self.od_enabled,
            "qwen_enabled": self.qwen_enabled,
            "od_running": _od_alive(),
            "ollama_model": s.get("ollama_model"),
            "vram_used_mb": s.get("vram_used_mb"),
            "vram_total_mb": s.get("vram_total_mb"),
            "last_batch": m.get("last_batch"),
            "last_compare": m.get("last_compare"),
            "last_od_probe": m.get("last_od_probe"),
        }


def _od_alive():
    try:
        r = httpx.get(f"{OD_URL}/health", timeout=1.0)
        return r.status_code == 200 and r.json().get("status") == "ok"
    except Exception:
        return False


def _start_od():
    import shutil
    if OD_BIN.exists():
        bin_path = str(OD_BIN)
    else:
        bin_path = shutil.which("opendecision")
        if not bin_path:
            raise RuntimeError(
                f"opendecision not found in {OD_BIN} or PATH. "
                f"Install it or run 'opendecision serve --port {OD_PORT}' manually."
            )
    OD_LOG.parent.mkdir(parents=True, exist_ok=True)
    print(f"  [runtime] starting OD (log -> {OD_LOG.name})",
          file=sys.stderr, flush=True)
    with open(OD_LOG, "a", encoding="utf-8") as log_fh:
        subprocess.Popen(
            [bin_path, "serve", "--port", str(OD_PORT)],
            stdout=log_fh, stderr=log_fh,
            start_new_session=True,
        )


def _wait_od():
    deadline = time.time() + OD_STARTUP_TIMEOUT
    while time.time() < deadline:
        if _od_alive():
            return
        time.sleep(0.5)
    raise RuntimeError(f"OD did not respond in {OD_STARTUP_TIMEOUT}s")


def stop_od():
    subprocess.run(
        ["pkill", "-f", f"opendecision serve --port {OD_PORT}"],
        check=False,
    )


def stop_all():
    stop_od()
    subprocess.run(["pkill", "-f", "ollama serve"], check=False)


def main():
    p = argparse.ArgumentParser(description="runtime control")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="JSON state")
    check = sub.add_parser("check", help="exit 0 if enabled")
    check.add_argument("which", choices=("od", "qwen"))
    ensure_p = sub.add_parser("ensure", help="bring LLM up")
    ensure_p.add_argument("which", choices=("od", "qwen"))
    sub.add_parser("stop", help="stop ollama + od")
    args = p.parse_args()

    if args.cmd == "stop":
        stop_all()
        print("stopped", file=sys.stderr)
        return

    ctl = LLMControl()

    if args.cmd == "status":
        print(json.dumps(ctl.status(), indent=2))
        return

    if args.cmd == "check":
        enabled = ctl.od_enabled if args.which == "od" else ctl.qwen_enabled
        sys.exit(0 if enabled else 1)

    if args.cmd == "ensure":
        if args.which == "od":
            if not ctl.od_enabled:
                sys.exit("od disabled (ALT_NO_OD=1)")
            ctl.ensure_od()
        else:
            if not ctl.qwen_enabled:
                sys.exit("qwen disabled (ALT_NO_QWEN=1)")
            model = ctl.cfg["routes"]["generic"]["model"]
            ctl.ensure_qwen(model)
        print(f"{args.which} ready", file=sys.stderr)


if __name__ == "__main__":
    main()
