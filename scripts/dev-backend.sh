#!/usr/bin/env bash
# Local backend dev server (expects `make infra` -> Postgres :5442, Redis :6390)
set -euo pipefail
cd "$(dirname "$0")/../backend"
export AIECC_LOG_JSON=${AIECC_LOG_JSON:-false}
.venv/bin/alembic upgrade head
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8100 --reload --reload-dir app
