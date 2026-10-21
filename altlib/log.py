"""JSON event logging and stderr stage timing."""
import json
import sys
from datetime import datetime
from pathlib import Path

from .paths import LOG_JSONL, relpath


def log_event(event, path=None):
    target = Path(path) if path else LOG_JSONL
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {**event, "ts": datetime.now().isoformat(timespec="milliseconds")}
    with open(target, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def log_stage(stage, seconds, file=None, **extra):
    seconds = round(seconds, 3)
    ts = datetime.now().strftime("%H:%M:%S")
    model = extra.get("model", "")
    model_tag = f" [{model}]" if model else ""
    print(f"  [{ts}] [{stage:<13}] {seconds:>7.2f}s{model_tag}",
          file=sys.stderr, flush=True)
    log_event({"event": "stage", "stage": stage, "seconds": seconds,
               "file": relpath(file), **extra})
