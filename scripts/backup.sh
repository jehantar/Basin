#!/usr/bin/env bash
# Daily Postgres backup — run inside collector container via cron
set -euo pipefail

BACKUP_DIR="/data/backups"
RETENTION_DAYS=30
TIMESTAMP=$(date -u +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/basin_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "[$(date -u)] Starting backup..."

# pg_dump via the postgres container over the Docker network
PGPASSWORD="${BASIN_PG_PASSWORD:-}" pg_dump \
    -h postgres \
    -U basin \
    -d basin \
    --no-owner \
    --no-privileges \
    | gzip > "$BACKUP_FILE"

echo "[$(date -u)] Backup written to $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"

# Remove backups older than retention period
find "$BACKUP_DIR" -name "basin_*.sql.gz" -mtime "+${RETENTION_DAYS}" -delete
echo "[$(date -u)] Cleaned up backups older than ${RETENTION_DAYS} days"
