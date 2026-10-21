"""System status: VRAM, CPU, current Ollama model."""
import os
import subprocess


def _vram_status():
    try:
        proc = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        )
        if proc.returncode != 0:
            return {}
        used, total = proc.stdout.strip().split(", ")
        return {"vram_used_mb": int(used), "vram_total_mb": int(total)}
    except Exception:
        return {}


def _cpu_status():
    try:
        load1, _, _ = os.getloadavg()
        ncpu = os.cpu_count() or 1
        return {"cpu_pct": round(100 * load1 / ncpu, 1)}
    except Exception:
        return {}


def _ollama_model_status():
    try:
        proc = subprocess.run(["ollama", "ps"],
                              capture_output=True, text=True, timeout=3)
        lines = proc.stdout.splitlines()
        for line in lines[1:]:
            parts = line.split()
            if parts:
                return {"ollama_model": parts[0]}
    except Exception:
        pass
    return {}


def system_status():
    return {**_vram_status(), **_cpu_status(), **_ollama_model_status()}


def system_status_text():
    status = system_status()
    parts = []
    if "vram_used_mb" in status:
        parts.append(f"vram {status['vram_used_mb']}/{status['vram_total_mb']} MB")
    if "cpu_pct" in status:
        parts.append(f"cpu {status['cpu_pct']}%")
    if "ollama_model" in status:
        parts.append(f"model {status['ollama_model']}")
    return " | ".join(parts) if parts else "(unavailable)"