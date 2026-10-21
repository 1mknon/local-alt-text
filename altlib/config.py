"""Config loading and validation — strict, no fallbacks."""
import json
import os
from pathlib import Path

from .paths import CONFIG_PATH

CONFIG_REQUIRED_ROUTE = ("model", "max_side", "num_predict", "prompt")


def load_config(path=None, strict=True):
    if path is None:
        env = os.environ.get("ALT_CFG", "").strip()
        path = env if env else CONFIG_PATH
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"config not found: {path}")
    with open(path, encoding="utf-8") as fh:
        try:
            cfg = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ValueError(f"config.json invalid JSON: {exc}") from exc
    if strict:
        validate_config(cfg)
    return cfg


def validate_config(cfg):
    errors = []
    general = cfg.get("general") or {}

    for key in ("classifier_model", "keep_alive", "metrics_csv",
                "comparison_csv", "downloads_dir", "examples_dir",
                "gajim_alt_marker"):
        if not str(general.get(key, "")).strip():
            errors.append(f"general.{key}: missing or empty")

    if "language_suffix" not in general:
        errors.append("general.language_suffix: missing")
    elif not isinstance(general["language_suffix"], str):
        errors.append("general.language_suffix: must be string")

    for key, expected in (
        ("max_alt_chars", int),
        ("ctx", int),
        ("od_conf_min", float),
        ("action_conf_min", float),
        ("action_prompt_min", float),
    ):
        value = general.get(key)
        if not isinstance(value, expected) or isinstance(value, bool):
            errors.append(f"general.{key}: must be {expected.__name__}")

    llms = general.get("llms")
    if not isinstance(llms, dict):
        errors.append("general.llms: must be object")
    else:
        for key in ("od", "qwen"):
            if not isinstance(llms.get(key), bool):
                errors.append(f"general.llms.{key}: must be bool")

    judge = general.get("judge")
    if not isinstance(judge, dict):
        errors.append("general.judge: must be object")
    elif not str(judge.get("model", "")).strip():
        errors.append("general.judge.model: missing or empty")

    apm = general.get("action_prompt_min")
    acm = general.get("action_conf_min")
    if isinstance(apm, float) and isinstance(acm, float) and apm < acm:
        errors.append("general.action_prompt_min must be >= general.action_conf_min")

    routes = cfg.get("routes") or {}
    if not routes:
        errors.append("routes: missing or empty")
    for name, route in routes.items():
        for key in CONFIG_REQUIRED_ROUTE:
            if not str(route.get(key, "")).strip():
                errors.append(f"routes.{name}.{key}: missing or empty")
        for key in ("max_side", "num_predict"):
            value = route.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                errors.append(f"routes.{name}.{key}: must be positive integer")

    if not isinstance(cfg.get("heuristics"), dict):
        errors.append("heuristics: missing or not an object")

    if errors:
        raise ValueError("config.json invalid:\n  " + "\n  ".join(errors))
    return cfg


def routes_with_suffix(cfg):
    suffix = cfg["general"]["language_suffix"].strip()
    keep_alive = cfg["general"]["keep_alive"]
    return {
        name: {
            **route,
            "keep_alive": keep_alive,
            "prompt": route["prompt"].strip()
                      + (f"\n\n{suffix}" if suffix else ""),
        }
        for name, route in cfg["routes"].items()
    }
