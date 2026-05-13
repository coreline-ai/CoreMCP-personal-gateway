#!/usr/bin/env bash
set -euo pipefail

COREMCP_DATA_DIR="${COREMCP_DATA_DIR:-$HOME/.coremcp}"
DB_PATH="${COREMCP_DB_PATH:-$COREMCP_DATA_DIR/data/coremcp.sqlite3}"
BACKUP_DIR="${COREMCP_BACKUP_DIR:-$COREMCP_DATA_DIR/backups}"
RETENTION_DAYS="${COREMCP_BACKUP_RETENTION_DAYS:-7}"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

if [[ ! -f "$DB_PATH" ]]; then
  echo "Database not found: $DB_PATH" >&2
  exit 1
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="$BACKUP_DIR/coremcp-$stamp.sqlite3"

sqlite3 "$DB_PATH" ".backup '$out'"
chmod 600 "$out"
find "$BACKUP_DIR" -name 'coremcp-*.sqlite3' -type f -mtime +"$RETENTION_DAYS" -delete

echo "$out"
