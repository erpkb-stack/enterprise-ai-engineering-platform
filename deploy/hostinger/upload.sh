#!/usr/bin/env bash
# Run on YOUR computer: copies the project to the VPS (no secrets, no build artefacts).
#   ./deploy/hostinger/upload.sh <VPS_IP> [ssh-user]
set -euo pipefail
VPS="${1:?usage: upload.sh <VPS_IP> [user]}"
USER_="${2:-root}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ssh "$USER_@$VPS" "mkdir -p /opt/aiecc"
rsync -az --delete \
  --exclude '.git' --exclude 'node_modules' --exclude '.venv' --exclude 'dist' --exclude '__pycache__' \
  --exclude '.pytest_cache' --exclude '.ruff_cache' --exclude '*.db' --exclude 'test-results' \
  --exclude 'playwright-report' --exclude 'scripts/loadtest/results' --exclude '.env' \
  --exclude '.env.production' --exclude 'backups' \
  "$ROOT/" "$USER_@$VPS:/opt/aiecc/"
echo "Uploaded to $USER_@$VPS:/opt/aiecc"
echo "Next:  ssh $USER_@$VPS 'cd /opt/aiecc && sudo ./deploy/hostinger/deploy.sh'"
