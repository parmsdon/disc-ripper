#!/bin/bash
# Nightly database backup for Discripper (production).
# Writes a pg_dump custom-format file to /mnt/datastore/backup/ and
# retains the 3 most recent successful dumps, deleting older ones only
# after a new dump succeeds.
#
# Usage:
#   DISCRIPPER_ENV=prod ./scripts/backup_db.sh
#   (called automatically by discripper-backup.timer)

set -euo pipefail

# ---------------------------------------------------------------------------
# Parse a value from config/prod.yaml
# ---------------------------------------------------------------------------
_yaml() {
    python3 -c "import yaml; c=yaml.safe_load(open('config/prod.yaml')); print($1)"
}

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
[[ -f config/prod.yaml ]] \
    || { echo "ERROR: config/prod.yaml not found" >&2; exit 1; }

mountpoint -q /mnt/datastore \
    || { echo "ERROR: /mnt/datastore is not a mountpoint — backup aborted" >&2; exit 1; }

BACKUP_DIR=/mnt/datastore/backup
mkdir -p "$BACKUP_DIR"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_HOST=$(_yaml "c['database']['host']")
DB_PORT=$(_yaml "c['database']['port']")
DB_NAME=$(_yaml "c['database']['name']")
DB_USER=$(_yaml "c['database']['user']")
export PGPASSWORD
PGPASSWORD=$(_yaml "c['database']['password']")

# ---------------------------------------------------------------------------
# Dump
# ---------------------------------------------------------------------------
DATESTAMP=$(date +%Y-%m-%d)
DEST="$BACKUP_DIR/discripper_${DATESTAMP}.dump"
TMP="${DEST}.tmp"

echo "$(date -Iseconds) Starting backup: $DB_NAME → $DEST"

pg_dump \
    --host="$DB_HOST" \
    --port="$DB_PORT" \
    --username="$DB_USER" \
    --format=custom \
    --file="$TMP" \
    "$DB_NAME"

# Verify the dump is non-empty before promoting it
DUMP_SIZE=$(stat -c %s "$TMP")
if [[ "$DUMP_SIZE" -eq 0 ]]; then
    rm -f "$TMP"
    echo "ERROR: pg_dump produced an empty file — backup aborted" >&2
    exit 1
fi

mv "$TMP" "$DEST"
echo "$(date -Iseconds) Backup complete: $DEST (${DUMP_SIZE} bytes)"

# ---------------------------------------------------------------------------
# Rotate: keep the 3 most recent dumps, delete the rest.
# Only runs after a successful dump — never deletes on failure.
# ---------------------------------------------------------------------------
mapfile -t OLD_DUMPS < <(
    ls -t "$BACKUP_DIR"/discripper_*.dump 2>/dev/null | tail -n +4
)
if [[ ${#OLD_DUMPS[@]} -gt 0 ]]; then
    for f in "${OLD_DUMPS[@]}"; do
        rm -f "$f"
        echo "$(date -Iseconds) Deleted old backup: $f"
    done
fi
