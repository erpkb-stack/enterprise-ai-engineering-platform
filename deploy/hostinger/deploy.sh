#!/usr/bin/env bash
# Run ON THE VPS (Ubuntu 22.04/24.04) from /opt/aiecc:   sudo ./deploy/hostinger/deploy.sh
# Idempotent: safe to re-run after every upload (rebuilds and restarts; data volumes are kept).
set -euo pipefail
cd "$(dirname "$0")/../.."
APP_DIR="$(pwd)"
ENV_FILE="$APP_DIR/.env.production"
DOMAIN_DEFAULT="aiecc.groundwire.tech"
log() { printf '\n\033[1;35m==> %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo/root"; exit 1; }

# 1. Docker -------------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  log "Installing Docker Engine + Compose plugin"
  apt-get update -y && apt-get install -y ca-certificates curl
  curl -fsSL https://get.docker.com | sh
fi
docker compose version >/dev/null || { echo "Docker Compose v2 plugin missing"; exit 1; }
systemctl enable --now docker >/dev/null 2>&1 || true

# 2. Secrets (generated once, then reused) ------------------------------------------------
if [ ! -f "$ENV_FILE" ]; then
  log "Generating $ENV_FILE with random secrets"
  read -r -p "Public domain [$DOMAIN_DEFAULT]: " DOMAIN; DOMAIN="${DOMAIN:-$DOMAIN_DEFAULT}"
  read -r -p "Email for Let's Encrypt notices: " EMAIL
  [ -n "$EMAIL" ] || { echo "Email is required"; exit 1; }
  read -r -p "Mode: 'staging' (public demo with demo users) or 'production' [staging]: " MODE; MODE="${MODE:-staging}"
  SEED=true; [ "$MODE" = "production" ] && SEED=false
  rnd() { openssl rand -base64 "$1" | tr -d '\n=+/' | cut -c1-"$2"; }
  DEMO_PW="Demo-$(rnd 24 12)-9a"
  cat > "$ENV_FILE" <<ENVEOF
AIECC_DOMAIN=$DOMAIN
ACME_EMAIL=$EMAIL
AIECC_CORS_ORIGINS=https://$DOMAIN
AIECC_ENV=$MODE
AIECC_SEED_DEMO_DATA=$SEED
AIECC_DEMO_PASSWORD=$DEMO_PW
POSTGRES_PASSWORD=$(openssl rand -hex 24)
AIECC_JWT_SECRET=$(rnd 64 64)
AIECC_ENCRYPTION_KEY=$(openssl rand -base64 32 | tr '+/' '-_')
GRAFANA_ADMIN_PASSWORD=$(rnd 24 20)
AIECC_GEMINI_API_KEY=
AIECC_OPENAI_API_KEY=
AIECC_ANTHROPIC_API_KEY=
AIECC_NVIDIA_NIM_API_KEY=
AIECC_OTEL_ENABLED=false
AIECC_RATE_LIMIT_PER_MINUTE=600
ENVEOF
  chmod 600 "$ENV_FILE"
fi
set -a; . "$ENV_FILE"; set +a

# 3. Firewall: only SSH + HTTP(S) ---------------------------------------------------------
if command -v ufw >/dev/null 2>&1; then
  log "Configuring firewall (22, 80, 443)"
  ufw allow 22/tcp >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ufw allow 443/udp >/dev/null
  ufw --force enable >/dev/null
fi

# 4. DNS sanity check ---------------------------------------------------------------------
PUBLIC_IP="$(curl -fsS https://api.ipify.org || true)"
DNS_IP="$(getent hosts "$AIECC_DOMAIN" | awk '{print $1}' | head -1 || true)"
if [ -n "$PUBLIC_IP" ] && [ "$DNS_IP" != "$PUBLIC_IP" ]; then
  echo "WARNING: $AIECC_DOMAIN resolves to '${DNS_IP:-nothing}', this server is $PUBLIC_IP."
  echo "         Add a DNS A record (name: ${AIECC_DOMAIN%%.*}, value: $PUBLIC_IP). HTTPS issuance will retry until it matches."
fi

# 5. Build & start --------------------------------------------------------------------------
PROFILE="${AIECC_PROFILE:-app}"   # set AIECC_PROFILE=full for OTel+Prometheus+Grafana (needs 8 GB RAM)
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file "$ENV_FILE" --profile "$PROFILE")
log "Building images (first run takes a few minutes)"
"${COMPOSE[@]}" build
log "Starting stack (profile: $PROFILE)"
"${COMPOSE[@]}" up -d --remove-orphans

# 6. Health -----------------------------------------------------------------------------------
log "Waiting for the API to become healthy"
for i in $(seq 1 60); do
  if "${COMPOSE[@]}" ps backend --format '{{.Status}}' | grep -q '(healthy)'; then break; fi
  sleep 5
done
"${COMPOSE[@]}" ps --format 'table {{.Name}}\t{{.Status}}'
"${COMPOSE[@]}" exec -T backend python -c "import urllib.request,json;print(json.load(urllib.request.urlopen('http://localhost:8100/ready'))['database'])"

# 7. Nightly backups ---------------------------------------------------------------------
CRON="/etc/cron.d/aiecc-backup"
if [ ! -f "$CRON" ]; then
  echo "15 3 * * * root $APP_DIR/deploy/hostinger/backup.sh >> /var/log/aiecc-backup.log 2>&1" > "$CRON"
  chmod 644 "$CRON"
fi

log "Done:  https://$AIECC_DOMAIN"
if [ "$AIECC_SEED_DEMO_DATA" = "true" ]; then
  echo "Demo users: admin@aiecc.local, aieng@aiecc.local, dev@aiecc.local, security@aiecc.local, auditor@aiecc.local, viewer@aiecc.local"
  echo "Demo password (stored in $ENV_FILE): $AIECC_DEMO_PASSWORD"
fi
