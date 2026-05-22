#!/usr/bin/env bash
# backup-restore-drill — verify backup-sqlite.sh + restore-sqlite.sh round-trip.
#
# Usage: infra/scripts/backup-restore-drill.sh
#
# Creates an isolated temporary SQLite DB with one schema + row, runs
# backup-sqlite.sh against it, restores it into a separate location, then
# compares row counts and the sqlite3 .dump hash. Exits 0 on success.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
work_dir="$(mktemp -d -t coremcp-drill-XXXXXX)"
trap 'rm -rf "$work_dir"' EXIT

src_db="$work_dir/source.sqlite3"
backup_dir="$work_dir/backups"
restored_db_root="$work_dir/restored-root"

mkdir -p "$backup_dir"

# 1) seed source DB with a known schema + row
sqlite3 "$src_db" <<'SQL'
CREATE TABLE drill (id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
INSERT INTO drill (payload) VALUES ('alpha'), ('beta'), ('gamma');
SQL

src_hash="$(sqlite3 "$src_db" .dump | shasum -a 256 | awk '{print $1}')"
src_rows="$(sqlite3 "$src_db" 'SELECT COUNT(*) FROM drill;')"

# 2) run backup-sqlite.sh against the temp DB
out=$(
  COREMCP_DB_PATH="$src_db" \
  COREMCP_BACKUP_DIR="$backup_dir" \
  "$PROJECT_ROOT/infra/scripts/backup-sqlite.sh"
)

if [[ ! -f "$out" ]]; then
  echo "drill FAIL: backup-sqlite.sh did not produce a file ($out)" >&2
  exit 1
fi

# 3) restore into a fresh location
dest_db="$restored_db_root/restored.sqlite3"
COREMCP_DB_PATH="$dest_db" \
  "$PROJECT_ROOT/infra/scripts/restore-sqlite.sh" "$out" >/dev/null

# 4) compare row count + .dump hash
restored_hash="$(sqlite3 "$dest_db" .dump | shasum -a 256 | awk '{print $1}')"
restored_rows="$(sqlite3 "$dest_db" 'SELECT COUNT(*) FROM drill;')"

if [[ "$src_rows" != "$restored_rows" ]]; then
  echo "drill FAIL: row count mismatch (source=$src_rows restored=$restored_rows)" >&2
  exit 1
fi
if [[ "$src_hash" != "$restored_hash" ]]; then
  echo "drill FAIL: .dump hash mismatch" >&2
  echo "  source   = $src_hash" >&2
  echo "  restored = $restored_hash" >&2
  exit 1
fi

echo "drill OK: rows=$src_rows hash=${src_hash:0:12}..."
