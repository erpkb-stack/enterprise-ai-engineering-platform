#!/usr/bin/env bash
# Nightly PostgreSQL backup (installed as a cron job by deploy.sh). Keeps the last 7 dumps.
#   Restore:  gunzip -c backups/aiecc-YYYYmmdd-HHMM.sql.gz | docker compose -f docker-compose.yml \
#             -f docker-compose.prod.yml --env-file .env.production exec -T postgres psql -U aiecc -d aiecc
set -euo pipefail
cd "$(dirname "$0")/../.."
mkdir -p backups
F="backups/aiecc-$(date +%Y%m%d-%H%M).sql.gz"
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production \
  exec -T postgres pg_dump -U aiecc -d aiecc --no-owner | gzip > "$F"
chmod 600 "$F"
ls -1t backups/aiecc-*.sql.gz | tail -n +8 | xargs -r rm -f
echo "$(date -Is) backup ok: $F ($(du -h "$F" | cut -f1))"
