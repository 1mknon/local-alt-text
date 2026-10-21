"""OD contract files: <image>.od.json — product of extract_facts, query of alt."""
import hashlib
import json
from datetime import datetime
from pathlib import Path

from .paths import relpath, sha256_file


def text_sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def od_path(image_path):
    return Path(str(image_path) + ".od.json")


def read_od(image_path):
    """Return OD dict if contract exists, sha256 matches, and shape is current."""
    p = od_path(image_path)
    if not p.is_file():
        return None
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("image_sha256") != sha256_file(image_path):
        return None
    if "visual_type" not in data:
        return None
    return data


def write_od(image_path, type_ans, action_ans=None, model=None):
    if model is None:
        raise ValueError("write_od: model is required")
    p = od_path(image_path)
    data = {
        "image": Path(image_path).name,
        "image_path": relpath(str(image_path)),
        "image_sha256": sha256_file(image_path),
        "model": model,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "visual_type": {
            "choice": type_ans.get("choice", ""),
            "probabilities": type_ans.get("probabilities", {}),
            "confidence": type_ans.get("confidence", 0.0),
        },
    }
    if action_ans is not None:
        data["action"] = {
            "choice": action_ans.get("choice", ""),
            "probabilities": action_ans.get("probabilities", {}),
            "confidence": action_ans.get("confidence", 0.0),
        }
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return data
