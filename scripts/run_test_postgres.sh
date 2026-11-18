#!/usr/bin/env bash
# Start or stop the optional local test PostgreSQL (127.0.0.1 + password, never 0.0.0.0).
# Usage:
#   bash scripts/run_test_postgres.sh start
#   bash scripts/run_test_postgres.sh stop
#   source /tmp/oiro-pgtest/env.sh   # exports OIRO_TEST_DATABASE_URL without printing it
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${ROOT}/.venv/bin/python"
cd "${ROOT}/backend"
if [[ "${1:-start}" == "stop" ]]; then
  "$PY" -c "from tests.pg_harness import stop; stop()"
  echo "test postgres stopped"
  exit 0
fi
"$PY" - <<'PY'
from pathlib import Path
from tests.pg_harness import start, URL_FILE, HARNESS_DIR
from app.database import redact_database_url

url = start()
env_path = HARNESS_DIR / "env.sh"
env_path.write_text(f"export OIRO_TEST_DATABASE_URL=$(cat {URL_FILE})\n", encoding="utf-8")
env_path.chmod(0o600)
print("test postgres listening on 127.0.0.1 only")
print("DATABASE_URL (redacted):", redact_database_url(url))
print("export the URL with: source /tmp/oiro-pgtest/env.sh")
print("then: cd backend && ../.venv/bin/python -m pytest -q")
PY
