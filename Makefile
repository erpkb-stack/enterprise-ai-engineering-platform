SHELL := /bin/bash
BACKEND := backend
FRONTEND := frontend
PY := $(BACKEND)/.venv/bin/python

.PHONY: help setup infra infra-down migrate seed dev backend frontend worker test test-backend test-pg test-frontend e2e lint typecheck audit loadtest up up-full down mcp-http clean

help:  ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n",$$1,$$2}'

setup:  ## Install backend (uv) + frontend (npm) dependencies
	./scripts/setup.sh

infra:  ## Start PostgreSQL (pgvector) + Redis in Docker
	docker compose up -d postgres redis

infra-down:  ## Stop infra containers (data volume kept)
	docker compose stop postgres redis

migrate:  ## Apply Alembic migrations
	cd $(BACKEND) && .venv/bin/alembic upgrade head

seed:  ## Seed RBAC + DEMO data (idempotent)
	cd $(BACKEND) && .venv/bin/python -m app.seed.demo

backend:  ## Run API with autoreload on :8100
	cd $(BACKEND) && .venv/bin/alembic upgrade head && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8100 --reload --reload-dir app

quickstart:  ## Run API with SQLite + in-memory fallbacks (no Docker)
	cd $(BACKEND) && AIECC_DATABASE_URL=sqlite+aiosqlite:///./aiecc-quickstart.db AIECC_REDIS_URL= .venv/bin/uvicorn app.main:app --port 8100

frontend:  ## Run Vite dev server on :5180 (proxies /api to :8100)
	cd $(FRONTEND) && npm run dev

worker:  ## Run ARQ background worker (set AIECC_JOBS_MODE=arq)
	cd $(BACKEND) && .venv/bin/arq app.worker.WorkerSettings

mcp-http:  ## Run the demo MCP server over Streamable HTTP on :8765 (X-API-Key: demo-mcp-key)
	cd $(BACKEND) && .venv/bin/python app/integrations/mcp/demo_server.py --http 8765

test: test-backend test-frontend  ## Run all automated tests

test-backend:  ## Backend tests on SQLite (fast, no Docker)
	cd $(BACKEND) && .venv/bin/python -m pytest -q

test-pg:  ## Backend tests on PostgreSQL + pgvector + Alembic (requires `make infra`)
	docker compose exec -T postgres psql -U aiecc -d aiecc -tc "SELECT 1 FROM pg_database WHERE datname='aiecc_test'" | grep -q 1 || docker compose exec -T postgres psql -U aiecc -d aiecc -c "CREATE DATABASE aiecc_test"
	cd $(BACKEND) && TEST_DATABASE_URL=postgresql+asyncpg://aiecc:aiecc@localhost:5442/aiecc_test AIECC_DATABASE_URL=postgresql+asyncpg://aiecc:aiecc@localhost:5442/aiecc_test .venv/bin/python -m pytest -q

test-ollama:  ## Real-model test via local Ollama (llama3.2:3b)
	cd $(BACKEND) && TEST_OLLAMA=1 .venv/bin/python -m pytest -q -k ollama tests/ai

test-frontend:  ## Vitest + Testing Library
	cd $(FRONTEND) && npx vitest run

e2e:  ## Playwright E2E (stack must be running; installs chromium on first run)
	cd $(FRONTEND) && npx playwright install chromium --only-shell && npx playwright test

lint:  ## Ruff (backend) + TypeScript (frontend)
	cd $(BACKEND) && .venv/bin/ruff check app tests && .venv/bin/ruff format --check app
	cd $(FRONTEND) && npx tsc -b

audit:  ## Dependency vulnerability scanning
	cd $(BACKEND) && .venv/bin/pip-audit --skip-editable || true
	cd $(FRONTEND) && npm audit --omit=dev || true

loadtest:  ## Locust: 50 users for 2 minutes against :8100
	cd $(BACKEND) && .venv/bin/locust -f ../scripts/loadtest/locustfile.py --host http://localhost:8100 --headless -u 50 -r 10 -t 2m --csv ../scripts/loadtest/results/run

up:  ## Full app stack in Docker (api, worker, web on :8180)
	docker compose --profile app up -d --build

up-full:  ## Full stack incl. OTel collector, Prometheus (:9190), Grafana (:3190)
	docker compose --profile full up -d --build

down:  ## Stop all containers
	docker compose --profile full down

clean:  ## Remove local build/test artefacts
	rm -rf $(FRONTEND)/dist $(BACKEND)/.pytest_cache $(BACKEND)/aiecc-quickstart.db
