#!/usr/bin/env bash
set -euo pipefail

COMMAND="${1:-status}"
PLIST_DIR="${COREMCP_PLIST_DIR:-$(pwd)/infra/launchd}"
API_PLIST="$PLIST_DIR/com.coremcp.api.plist"
WEB_PLIST="$PLIST_DIR/com.coremcp.web.plist"
FAKE_PLIST="$PLIST_DIR/com.coremcp.fake-mcp.plist"
BACKUP_PLIST="$PLIST_DIR/com.coremcp.backup.plist"
LOGROTATE_PLIST="$PLIST_DIR/com.coremcp.logrotate.plist"

usage() {
  echo "Usage: $0 {load|unload|restart|status}" >&2
}

case "$COMMAND" in
  load)
    launchctl load -w "$FAKE_PLIST"
    launchctl load -w "$API_PLIST"
    launchctl load -w "$WEB_PLIST"
    launchctl load -w "$BACKUP_PLIST"
    launchctl load -w "$LOGROTATE_PLIST"
    ;;
  unload)
    launchctl unload -w "$LOGROTATE_PLIST" 2>/dev/null || true
    launchctl unload -w "$BACKUP_PLIST" 2>/dev/null || true
    launchctl unload -w "$WEB_PLIST" 2>/dev/null || true
    launchctl unload -w "$API_PLIST" 2>/dev/null || true
    launchctl unload -w "$FAKE_PLIST" 2>/dev/null || true
    ;;
  restart)
    "$0" unload
    "$0" load
    ;;
  status)
    launchctl list | grep -E 'com\.coremcp\.(api|web|fake-mcp|backup|logrotate)' || true
    ;;
  *)
    usage
    exit 64
    ;;
esac
