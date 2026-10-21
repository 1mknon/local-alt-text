"""Shared library package: paths, config, logging, status, LLM, image."""
from .paths import (
    BASE_DIR, CONFIG_PATH, VOCAB_PATH, ACOES_PATH, REFERENCIAS_PATH,
    LOG_JSONL,
    OLLAMA_URL, sha256_file, relpath, resolve_dir,
)
from .config import (
    CONFIG_REQUIRED_ROUTE,
    load_config, validate_config, routes_with_suffix,
)
from .log import log_event, log_stage
from .status import system_status, system_status_text
from .llm import (
    ensure_ollama, unload_ollama_model, llm_generate,
)
from .image import resize_for_model, encode_image
from .routing import TYPE_TO_ROUTE
from . import contract, manifest
