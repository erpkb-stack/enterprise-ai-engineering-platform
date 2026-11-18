#!/usr/bin/env bash
# Reproducible local setup. Requires: Python 3.12+, uv, Node 20+, Docker (optional: Ollama).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/"; exit 1; }
uv venv --python 3.12 .venv
uv pip install -p .venv/bin/python -e ".[dev]"
[ -f .env ] || cp "$ROOT/.env.example" .env
cd "$ROOT/frontend"
npm ci --no-audit --no-fund || npm install --no-audit --no-fund
echo
echo "Setup complete. Next:"
echo "  make infra      # Postgres+pgvector & Redis (Docker)"
echo "  make backend    # API on http://localhost:8100 (migrates + seeds demo data)"
echo "  make frontend   # UI  on http://localhost:5180"
echo "No Docker? Use 'make quickstart' instead of infra+backend."
