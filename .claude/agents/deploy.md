---
name: deploy
description: Deploy code changes to the Basin VM. Use after editing collector or webhook files that need to go live. Handles the SCP + docker cp + restart workflow.
tools: Bash, Read, Grep, Glob
---

You deploy code changes from the local repo to the Basin VM.

## Deployment Steps

Basin code is baked into Docker images, NOT volume-mounted. To deploy a file change:

1. **SCP the file to the VM:**
   ```bash
   scp <local_path> root@Basin:/opt/basin/<relative_path>
   ```

2. **Docker cp into the running container:**
   - For webhook files: `ssh root@Basin "docker cp /opt/basin/<path> basin-webhook-1:/app/<path>"`
   - For collector files: `ssh root@Basin "docker cp /opt/basin/<path> basin-collector-1:/app/<path>"`
   - For shared files: cp into BOTH containers

3. **Restart the affected service:**
   ```bash
   ssh root@Basin "cd /opt/basin && docker compose restart webhook"
   # or: docker compose restart collector
   ```

## Critical Rules

- **NEVER run bare `docker compose up -d`** — it won't resolve 1Password `op://` secret references
- To recreate containers (e.g., after docker-compose.yml changes): `ssh root@Basin "systemctl restart basin.service"`
- Dashboard HTML files go to `/app/webhook/*.html` via the webhook container
- Always verify after deploy by hitting the health endpoint or dashboard

## Secrets

- The `.env` file uses `op://Vault/Item/field` references, not plaintext
- Secrets are injected via `op run` through the `basin.service` systemd unit
- NEVER store or transmit plaintext secrets

## Verification

After deploying, confirm the change is live:
- Webhook: `ssh root@Basin "docker exec basin-webhook-1 cat /app/<path>" | head -5`
- Health check: `curl -s http://100.125.126.42:8075/health`
- Dashboard: `curl -s http://100.125.126.42:8075/dashboard/fitness | head -20`
