#!/usr/bin/env bash
# Start the OIRO backend (8000) and frontend (5173) together.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv"
if [[ ! -x "$VENV/bin/python" ]]; then
  python3 -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install -q --upgrade pip
python -m pip install -q -r "$ROOT/backend/requirements.txt"

if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
  (cd "$ROOT/frontend" && npm install)
fi

mkdir -p "$ROOT/data"
export PYTHONPATH="$ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"

cleanup() {
  if [[ -n "${BACK_PID:-}" ]]; then kill "$BACK_PID" 2>/dev/null || true; fi
  if [[ -n "${FRONT_PID:-}" ]]; then kill "$FRONT_PID" 2>/dev/null || true; fi
  if [[ -n "${WORKER_PID:-}" ]]; then kill "$WORKER_PID" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM

"$VENV/bin/uvicorn" app.main:app --app-dir "$ROOT/backend" --host 127.0.0.1 --port 8000 &
BACK_PID=$!

(cd "$ROOT/frontend" && npm run dev) &
FRONT_PID=$!

# Optional local job worker. API and frontend still run if this is skipped or exits.
if [[ "${OIRO_DEV_WORKER:-1}" != "0" ]]; then
  "$VENV/bin/python" -m app.jobs.worker &
  WORKER_PID=$!
fi

echo
echo "OIRO 已启动："
echo "  前端  http://127.0.0.1:5173"
echo "  后端  http://127.0.0.1:8000"
echo "  健康检查  http://127.0.0.1:8000/api/health"
echo "  演示账号  admin / admin123"
if [[ -n "${WORKER_PID:-}" ]]; then
  echo "  本地任务进程已尝试启动（不需要 Redis；设 OIRO_DEV_WORKER=0 可关闭）"
fi
echo "按 Ctrl+C 同时停止。"
echo
wait
