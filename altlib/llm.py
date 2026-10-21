"""Ollama lifecycle and generation."""
import subprocess
import sys
import time

from .paths import OLLAMA_URL
from .log import log_event
from .status import _vram_status, system_status


def _ollama_alive(timeout=1.0):
    import httpx
    try:
        return httpx.get(f"{OLLAMA_URL}/api/tags", timeout=timeout).status_code == 200
    except Exception:
        return False


def ensure_ollama(timeout=30.0):
    if _ollama_alive():
        return
    subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _ollama_alive():
            return
        time.sleep(0.5)
    raise RuntimeError(f"ollama did not start within {timeout}s")


def unload_ollama_model(model, timeout=15.0):
    import httpx
    try:
        httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": model, "keep_alive": 0},
            timeout=timeout,
        )
    except Exception:
        pass



def llm_generate(model, prompt, images=None, system=None,
                 keep_alive="60m", num_predict=200, num_ctx=8192,
                 temperature=0.1):
    import ollama

    loaded = system_status().get("ollama_model")
    swap_seconds = 0.0
    if loaded and loaded != model:
        print(f"[swap] {loaded} -> {model} (will cost ~40s)",
              file=sys.stderr, flush=True)
        swap_t0 = time.time()
        try:
            ollama.generate(model=model, prompt="warmup", keep_alive=keep_alive,
                            options={"num_predict": 1})
        except Exception as exc:
            print(f"[swap] failed: {exc}", file=sys.stderr)
        swap_seconds = round(time.time() - swap_t0, 3)
        log_event({"event": "swap", "from": loaded, "to": model,
                   "seconds": swap_seconds})

    t0 = time.time()
    response = ollama.generate(
        model=model, prompt=prompt, images=images or [], system=system,
        keep_alive=keep_alive,
        options={"num_ctx": num_ctx, "num_predict": num_predict,
                 "temperature": temperature, "top_p": 0.9, "repeat_penalty": 1.15},
    )
    wall = round(time.time() - t0, 3)
    prompt_s = round(response.get("prompt_eval_duration", 0) / 1e9, 3) or 0.001
    gen_s = round(response.get("eval_duration", 0) / 1e9, 3) or 0.001
    metrics = {
        "model": model,
        "wall_seconds": wall,
        "swap_seconds": swap_seconds,
        "load_seconds": round(response.get("load_duration", 0) / 1e9, 3),
        "prompt_tokens": response.get("prompt_eval_count", 0),
        "generated_tokens": response.get("eval_count", 0),
        "prompt_seconds": prompt_s,
        "generation_seconds": gen_s,
        "prompt_tokens_per_s": round(response.get("prompt_eval_count", 0) / prompt_s, 1),
        "generation_tokens_per_s": round(response.get("eval_count", 0) / gen_s, 1),
        **_vram_status(),
    }
    log_event({"event": "llm", **metrics})
    return response["response"].strip(), metrics