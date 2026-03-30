#!/usr/bin/env bash
# Sync Apple Health export and/or Hevy CSV to Basin VM and run collectors.
# Usage:
#   ./scripts/sync-health.sh                    # sync both if files exist
#   ./scripts/sync-health.sh health             # sync health only
#   ./scripts/sync-health.sh hevy               # sync hevy only
set -euo pipefail

VM="root@reservebot"
BASIN="/opt/basin"

sync_health() {
    local export_dir="$HOME/Downloads/apple_health_export"
    if [ ! -f "$export_dir/export.xml" ]; then
        echo "No export.xml found at $export_dir"
        echo "Export from iPhone: Health > Profile > Export All Health Data"
        return 1
    fi
    echo "Uploading HealthKit export..."
    scp "$export_dir/export.xml" "$VM:$BASIN/data/healthkit/imports/"
    echo "Running HealthKit collector..."
    ssh "$VM" "cd $BASIN && docker compose exec collector python -m collectors.healthkit"
    echo "Done."
}

sync_hevy() {
    local csv=$(ls -t "$HOME/Downloads"/workout_data*.csv 2>/dev/null | head -1)
    if [ -z "$csv" ]; then
        echo "No Hevy CSV found in ~/Downloads/"
        echo "Export from Hevy: Profile > Settings > Export & Import Data > Export Workouts"
        return 1
    fi
    echo "Uploading Hevy CSV: $(basename "$csv")"
    scp "$csv" "$VM:$BASIN/data/hevy/drop/"
    echo "Running Hevy collector..."
    ssh "$VM" "cd $BASIN && docker compose exec collector python -m collectors.hevy"
    echo "Done."
}

case "${1:-both}" in
    health) sync_health ;;
    hevy)   sync_hevy ;;
    both)   sync_health; sync_hevy ;;
    *)      echo "Usage: $0 [health|hevy|both]" ;;
esac

echo ""
ssh "$VM" "cd $BASIN && docker compose exec collector python -m cli.health"
