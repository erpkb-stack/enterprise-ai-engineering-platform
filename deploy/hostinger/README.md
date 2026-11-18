# Deploy AIECC to a Hostinger VPS at https://aiecc.groundwire.tech

Target: Hostinger **KVM VPS** (Ubuntu 24.04, with or without the Docker template), 8 GB RAM recommended (KVM 2).
Result: one public HTTPS site; Postgres, Redis, the API and monitoring are not reachable from the internet.

```
Internet ──443──► Caddy (Let's Encrypt) ──► nginx (SPA + /api proxy + WebSocket) ──► FastAPI ──► Postgres+pgvector
                                                                                  └─► Redis ◄── ARQ worker
```

## 1. DNS (Hostinger hPanel → Domains → groundwire.tech → DNS / Nameservers)
Add an **A record**:

| Type | Name | Points to | TTL |
|---|---|---|---|
| A | `aiecc` | *your VPS IPv4 address* (hPanel → VPS → Overview) | 300 |

Check from your computer: `dig +short aiecc.groundwire.tech` should print the VPS IP (can take a few minutes).

## 2. Copy the project to the VPS
Pick one.

**A. From your Mac (rsync over SSH)** — uses the VPS root password or your SSH key:
```bash
./deploy/hostinger/upload.sh <VPS_IP>
```

**B. Via GitHub** — push this repo to a **private** GitHub repository, then on the VPS
(hPanel → VPS → *Browser terminal*, or `ssh root@<VPS_IP>`):
```bash
git clone https://github.com/<you>/<repo>.git /opt/aiecc
```
(For a private repo use a fine-grained personal access token with read-only access to that repo.)

> The hPanel **Docker Manager** "deploy from URL" flow is not used: AIECC builds its own images and needs
> generated secrets, a firewall and a migration step, which `deploy.sh` handles. Docker Manager will still show the
> running containers afterwards.

## 3. Deploy (on the VPS)
```bash
cd /opt/aiecc
sudo ./deploy/hostinger/deploy.sh
```
It will ask for the domain (default `aiecc.groundwire.tech`), an email for Let's Encrypt, and the mode:

- `staging` (default) — public **demo**: seeds the demo organisation and users with a **new random password**
  (printed at the end and stored in `/opt/aiecc/.env.production`).
- `production` — no demo data.

What it does: installs Docker if missing → generates `.env.production` (DB password, JWT secret, Fernet key,
demo password; `chmod 600`) → firewall allows only 22/80/443 → builds images → starts Postgres, Redis, API
(runs migrations), worker, nginx and Caddy → waits for health → installs a nightly backup cron (03:15, keeps 7).

The first build takes ~5–10 minutes. Then open **https://aiecc.groundwire.tech**.

The app refuses to start in `staging`/`production` with any development default secret or the published demo password.

## 4. Optional
- **Real AI model:** put an API key in `/opt/aiecc/.env.production` (e.g. `AIECC_GEMINI_API_KEY=...`), re-run `deploy.sh`,
  then set an agent's provider/model in the UI. Without keys the deterministic mock provider (labelled MOCK) is used.
- **Monitoring:** `sudo AIECC_PROFILE=full ./deploy/hostinger/deploy.sh` adds OTel, Prometheus and Grafana (needs 8 GB RAM).
  Grafana is bound to the VPS loopback only: `ssh -L 3190:localhost:3190 root@<VPS_IP>` → http://localhost:3190
  (admin password in `.env.production`).

## Update to a new version
Upload/pull the new code, then run `sudo ./deploy/hostinger/deploy.sh` again (secrets and data are kept).

## Operations
```bash
cd /opt/aiecc
C="docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production --profile app"
$C ps                       # status
$C logs -f backend          # API logs (also: worker, caddy, frontend)
$C restart backend
./deploy/hostinger/backup.sh  # manual backup → backups/
$C down                     # stop (data volumes kept)
```

## Troubleshooting
| Symptom | Fix |
|---|---|
| Browser shows a certificate error | DNS not pointing at the VPS yet, or ports 80/443 blocked. Check `dig`, then `$C logs caddy`; Caddy retries automatically. |
| `Refusing to start a publicly hosted instance` in backend logs | A secret in `.env.production` is a dev default — delete the file and re-run `deploy.sh`, or set a real value. |
| Build fails with out-of-memory | Use an 8 GB plan, or add swap: `fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile`. |
| Login says "Account temporarily locked" | 5 failed attempts → 15-minute lock (by design). |
