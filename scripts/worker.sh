#!/usr/bin/env bash
# Standalone local job worker (scheduled ingest, write retry, reconcile).
# Does not require Redis. If CELERY_BROKER_URL / REDIS_URL is set, this script
# exits and prints the Celery commands unless OIRO_JOB_QUEUE=local.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv"
if [[ ! -x "$VENV/bin/python" ]]; then
  python3 -m venv "$VENV"
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  python -m pip install -q --upgrade pip
  python -m pip install -q -r "$ROOT/backend/requirements.txt"
fi

export PYTHONPATH="$ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV/bin/python" -m app.jobs.worker "$@"
