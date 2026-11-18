#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export OIRO_DISTRIBUTION="${OIRO_DISTRIBUTION:-source}"
export OIRO_STATIC_ROOT="${OIRO_STATIC_ROOT:-$ROOT/frontend/dist}"
cd "$ROOT"
if [[ ! -d frontend/dist ]]; then
  (cd frontend && npm ci && npm run build)
fi
bash scripts/dev.sh