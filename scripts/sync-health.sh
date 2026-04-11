#!/usr/bin/env bash
# Sync Apple Health export and/or Hevy CSV to Basin VM and run collectors.
# Usage:
#   ./scripts/sync-health.sh                    # sync both if files exist
#   ./scripts/sync-health.sh health             # sync health only
#   ./scripts/sync-health.sh hevy               # sync hevy only
set -euo pipefail

VM="${BASIN_VM:-root@Basin}"
BASIN="/opt/basin"
EXPORT_DIR="$HOME/Desktop/Basin Exports"

sync_health() {
    # Auto-unzip if export.zip exists but export.xml doesn't
    if [ -f "$EXPORT_DIR/export.zip" ] && [ ! -f "$EXPORT_DIR/export.xml" ] && [ ! -f "$EXPORT_DIR/apple_health_export/export.xml" ]; then
        echo "Found export.zip — unzipping..."
        unzip -o "$EXPORT_DIR/export.zip" -d "$EXPORT_DIR"
    fi

    # Look for export.xml directly in the folder, or inside a subfolder
    local xml=""
    if [ -f "$EXPORT_DIR/export.xml" ]; then
        xml="$EXPORT_DIR/export.xml"
    elif [ -f "$EXPORT_DIR/apple_health_export/export.xml" ]; then
        xml="$EXPORT_DIR/apple_health_export/export.xml"
    fi

    if [ -z "$xml" ]; then
        echo "No export.xml found in $EXPORT_DIR"
        echo ""
        echo "Steps:"
        echo "  1. iPhone: Health > Profile pic > Export All Health Data"
        echo "  2. AirDrop or save the zip to ~/Desktop/Basin Exports/"
        echo "  3. Run this script again (zip is auto-extracted)"
        return 1
    fi
    echo "Found: $xml"
    echo "Uploading HealthKit export..."
    scp "$xml" "$VM:$BASIN/data/healthkit/imports/"
    echo "Running HealthKit collector..."
    ssh "$VM" "cd $BASIN && docker compose exec collector python -m collectors.healthkit"
    echo "Done."
}

sync_hevy() {
    local csv=$(ls -t "$EXPORT_DIR"/workout_data*.csv 2>/dev/null | head -1)
    if [ -z "$csv" ]; then
        echo "No Hevy CSV found in $EXPORT_DIR"
        echo ""
        echo "Steps:"
        echo "  1. Hevy app: Profile > Settings > Export & Import Data > Export Workouts"
        echo "  2. Save the CSV to ~/Desktop/Basin Exports/"
        echo "  3. Run this script again"
        return 1
    fi
    echo "Found: $csv"
    echo "Uploading Hevy CSV..."
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
