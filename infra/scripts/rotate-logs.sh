#!/usr/bin/env bash
set -euo pipefail

COREMCP_DATA_DIR="${COREMCP_DATA_DIR:-$HOME/.coremcp}"
LOG_DIR="${COREMCP_LOG_DIR:-$COREMCP_DATA_DIR/logs}"
RETENTION_DAYS="${COREMCP_LOG_RETENTION_DAYS:-7}"

mkdir -p "$LOG_DIR"
find "$LOG_DIR" -name '*.log' -type f -size +10M -print0 | while IFS= read -r -d '' file; do
  gzip -f "$file"
done
find "$LOG_DIR" \( -name '*.log' -o -name '*.log.gz' \) -type f -mtime +"$RETENTION_DAYS" -delete
