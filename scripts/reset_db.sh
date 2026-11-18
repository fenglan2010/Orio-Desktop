#!/usr/bin/env bash
# Wipe SQLite and reseed the demo hotel. This clears human-review history on purpose.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv"
if [[ ! -x "$VENV/bin/python" ]]; then
  python3 -m venv "$VENV"
  "$VENV/bin/python" -m pip install -q --upgrade pip
  "$VENV/bin/python" -m pip install -q -r "$ROOT/backend/requirements.txt"
fi
export PYTHONPATH="$ROOT/backend"
"$VENV/bin/python" -m app.seed --reset
echo "已重置 data/oiro.db 并写入演示数据。"
