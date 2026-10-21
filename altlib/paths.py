"""Absolute paths, constants, and file hashing.

Single source of truth for every path in the project. Any module that
needs a path imports it from here; nothing else composes BASE_DIR / x
by hand. If a path is missing here, add it here — do not hardcode.

The two mutable roots (downloads_dir, examples_dir) live in config.json
under general and are resolved by resolve_dir(). This keeps user hosts
and absolute filesystem layout out of the repository.
"""
import hashlib
import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent

# Config files
CONFIG_PATH      = BASE_DIR / "config.json"
VOCAB_PATH       = BASE_DIR / "vocabulario.json"
ACOES_PATH       = BASE_DIR / "acoes.json"
REFERENCIAS_PATH = BASE_DIR / "referencias.json"

# Generated artifacts
MANIFEST_JSON    = BASE_DIR / "manifest.json"
PAIRS_FILE       = BASE_DIR / "coletados.json"
JUDGE_CACHE_PATH = BASE_DIR / "comparados" / "judge_cache.json"

# Logs
LOG_DIR       = BASE_DIR / "logs"
LOG_JSONL     = LOG_DIR / "execucao.jsonl"
OD_OBS_JSONL  = LOG_DIR / "od_observacoes.jsonl"
OD_LOG        = LOG_DIR / "opendecision.log"

# OpenDecision
OD_BIN             = BASE_DIR / ".venv-opendecision" / "bin" / "opendecision"
OD_PORT            = 8090
OD_URL             = f"http://127.0.0.1:{OD_PORT}"
OD_STARTUP_TIMEOUT = 120.0

# Ollama
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")


def sha256_file(path, chunk=65536):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def relpath(path):
    if path is None:
        return None
    try:
        return str(Path(path).absolute().relative_to(BASE_DIR))
    except (ValueError, OSError):
        return Path(path).name


def resolve_dir(value):
    """Turn a config value into an absolute Path.

    Absolute values pass through. Relative values are anchored at BASE_DIR.
    The caller (validate_config) guarantees the string is non-empty.
    resolve_dir does NOT check existence — use .is_dir() at the call site.
    """
    p = Path(value)
    return p if p.is_absolute() else (BASE_DIR / p)
