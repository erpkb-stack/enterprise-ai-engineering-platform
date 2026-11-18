# Deployment

## Docker Compose profiles
| Command | Services |
|---|---|
| `docker compose up -d` (`make infra`) | postgres (pgvector), redis |
| `docker compose --profile app up -d --build` (`make up`) | + backend (runs `alembic upgrade head`), ARQ worker, nginx-served SPA on :8180 |
| `docker compose --profile full up -d --build` (`make up-full`) | + OTel collector, Prometheus (:9190), Grafana (:3190, "AIECC Overview" dashboard) |

Images: `backend/Dockerfile` (multi-stage, non-root, healthcheck), `frontend/Dockerfile` (Node build → nginx, CSP,
WebSocket proxy, `/metrics` not exposed).

## Production checklist
- Set `AIECC_ENV=production` (startup refuses the dev JWT secret), `AIECC_SEED_DEMO_DATA=false`.
- Provide `AIECC_JWT_SECRET`, `AIECC_ENCRYPTION_KEY`, DB/Redis URLs and provider keys from a secret manager.
- Managed PostgreSQL 16 with the `vector` extension; managed Redis.
- Run migrations as a separate step (init container / job), never `create_all`.
- Put the API behind TLS + WAF; restrict `/docs` if not desired publicly.
- Configure `AIECC_HTTP_TOOL_ALLOWED_HOSTS` and review Tool Registry allowlists.
- Enable OTel export and Prometheus scraping; wire alerts.

## Kubernetes
`infrastructure/deployment/k8s/aiecc.yaml`: namespace, config map, API Deployment (3 replicas, migration
init-container, probes, non-root, read-only root FS, dropped capabilities), worker Deployment, HPA (CPU 65 %), PDB,
Service. Secrets are expected from External Secrets / CSI.
