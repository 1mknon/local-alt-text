#!/bin/sh
set -e

cd "$(dirname "$0")"

echo "[install] python3 >= 3.11?"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    || { echo "need Python 3.11+"; exit 1; }

echo "[install] creating .venv"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate

echo "[install] pip deps"
pip install -q ollama Pillow httpx

echo "[install] opendecision"
if command -v opendecision >/dev/null 2>&1; then
    echo "  already installed: $(opendecision --version)"
elif command -v uv >/dev/null 2>&1; then
    uv tool install 'OpenDecision[images]'
elif command -v pipx >/dev/null 2>&1; then
    pipx install 'OpenDecision[images]'
else
    echo "  ERROR: install uv (https://astral.sh/uv) or pipx first" >&2
    exit 1
fi

echo "[install] ollama model"
if command -v ollama >/dev/null 2>&1; then
    ollama pull qwen2.5vl:7b
else
    echo "  WARN: ollama not in PATH. Install from https://ollama.com" >&2
fi

echo "[install] verify"
python3 alt.py --dry-run | head -3
echo ""
echo "[install] done."
echo "  Activate:  source .venv/bin/activate"
echo "  Calibration (optional): bring your own exemplos/ + referencias.json"
echo "  Run:       python3 alt.py --dry-run"