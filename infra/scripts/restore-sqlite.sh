#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 /path/to/coremcp-backup.sqlite3" >&2
  exit 64
fi

COREMCP_DATA_DIR="${COREMCP_DATA_DIR:-$HOME/.coremcp}"
DB_PATH="${COREMCP_DB_PATH:-$COREMCP_DATA_DIR/data/coremcp.sqlite3}"
BACKUP_PATH="$1"

if [[ ! -f "$BACKUP_PATH" ]]; then
  echo "Backup not found: $BACKUP_PATH" >&2
  exit 1
fi

mkdir -p "$(dirname "$DB_PATH")"
if [[ -f "$DB_PATH" ]]; then
  safety_copy="$DB_PATH.pre-restore-$(date -u +%Y%m%dT%H%M%SZ)"
  cp "$DB_PATH" "$safety_copy"
  chmod 600 "$safety_copy"
  echo "Saved current DB to $safety_copy"
fi

sqlite3 "$BACKUP_PATH" "PRAGMA integrity_check;" | grep -qx "ok"
cp "$BACKUP_PATH" "$DB_PATH"
chmod 600 "$DB_PATH"
echo "Restored $DB_PATH from $BACKUP_PATH"
