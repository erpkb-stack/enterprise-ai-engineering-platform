# Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `make infra` fails / `Cannot connect to the Docker daemon` | Docker Desktop not running (or crashed because the disk is full) | Start Docker Desktop; keep ≥ 5 GB free. Or use `make quickstart` (SQLite, no Docker). |
| Ports already in use | Other projects on 8000/5173/5432/6379 | AIECC uses 8100 (API), 5180 (UI), 5442 (Postgres), 6390 (Redis), 8180 (Docker UI). Override with `AIECC_*` env and compose port mappings. |
| `/ready` shows `redis: degraded` | Redis not reachable | Rate limiting, idempotency and cache fall back to in-memory (single process only). Start Redis for multi-replica correctness. |
| Login returns 401 for demo users | Demo data not seeded or wrong password | `make seed`; password is `AIECC_DEMO_PASSWORD` (default `Aiecc!Demo2026`). Note `/auth/login` is limited to 20/min/IP and accounts lock for 15 min after 5 failures. |
| Login returns 429 | Auth rate limit | Wait 60 s; for load tests share tokens across virtual users. |
| MCP connect fails `did not initialize` | STDIO server crashed or slow first start | Check the command/args; run the server manually (`make mcp-http` or `python app/integrations/mcp/demo_server.py`). Timeout is per server (`timeout_s`). |
| MCP tool call `POLICY DENIED: … not allowlisted` | Discovered tools are disabled by default | Enable in Tool Registry (requires `tools:manage`, e.g. Security Reviewer). |
| Agent run `max_steps_exceeded` | Model never produced a final action | Raise `max_steps` (≤ hard limit), simplify instructions, or use a stronger model. |
| Agent run `timeout` | Provider slow (e.g. CPU Ollama) | Increase `total_timeout_s` / `timeout_s`, or add a fallback route. |
| Ollama provider `unavailable` | Ollama not running / model not pulled | `ollama serve`; `ollama pull llama3.2:3b`. In Docker, the API reaches the host via `host.docker.internal:11434`. |
| Switching embeddings to Ollama returns dimension errors | Existing vectors were created by a different model | Keep `AIECC_EMBEDDING_DIM=768` (nomic-embed-text) and re-ingest documents; mixing embedding models in one index is not supported. |
| Evaluation run stuck in `queued` | `AIECC_JOBS_MODE=arq` without a worker | Run `make worker`, or use `inline` mode for local dev. |
| `audit_events is append-only` error | Something attempted UPDATE/DELETE on audit rows | Expected protection; audit rows are immutable. |
| Frontend 502 on `/api` | Backend not running on :8100 | `make backend`; Vite proxies `/api`, `/health`, `/ready` and the WebSocket. |
| Monaco editor slow to appear in dev | First load compiles a ~4 MB bundle | Normal in dev; production builds lazy-load it on pages that use it. |
| Slow responses under load on a laptop | Host CPU/RAM saturation (check `uptime`, swap) | Measure on dedicated hardware; scale API replicas/workers; see ENGINEERING_REPORT performance section. |
