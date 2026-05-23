API_URL ?= http://127.0.0.1:8787
ADMIN_TOKEN_FILE ?= $(HOME)/.coremcp/admin-token
COREMCP_DB_PATH ?= $(HOME)/.coremcp/data/coremcp.sqlite3
COREMCP_SECRETS_FILE ?= $(HOME)/.coremcp/data/secrets.json
FERNET_KEY_FILE ?= $(HOME)/.coremcp/data/secrets.key
BACKUP_ARCHIVE ?= $(HOME)/.coremcp/backups/coremcp-cli-export.tar
CLIENT_NAME ?= coremcp-cli

.PHONY: bootstrap run run-local stop status test test-api test-fake test-demo test-project-docs test-git-workspace demo-run lint build smoke ops-smoke external-env-validate soak-check mobile-qa-checklist route-smoke cli-doctor cli-service-list cli-token-issue cli-backup-export cli-backup-import-dry-run backup-restore-drill redis-smoke tailscale-acl-validate project-docs-register git-workspace-register codex-install codex-smoke codex-exec

bootstrap:
	infra/scripts/bootstrap-local.sh

run: bootstrap build
	infra/scripts/coremcp-launchctl.sh restart
	sleep 8
	infra/scripts/ops-smoke.sh

run-local: bootstrap build
	infra/scripts/run-local.sh

stop:
	infra/scripts/coremcp-launchctl.sh unload

status:
	infra/scripts/coremcp-launchctl.sh status

test: test-api test-fake test-demo test-project-docs test-git-workspace

test-api:
	cd apps/api && uv run pytest

test-fake:
	cd apps/fake-mcp && uv run pytest

test-demo:
	cd apps/demo-mcp-suite && uv run pytest

test-project-docs:
	cd apps/project-docs-mcp && uv run pytest

test-git-workspace:
	cd apps/git-workspace-mcp && uv run pytest

demo-run:
	cd apps/demo-mcp-suite && uv run demo-mcp-suite

lint:
	pnpm lint

build:
	pnpm build

smoke:
	cd apps/api && uv run python -m coremcp.smoke

ops-smoke:
	infra/scripts/ops-smoke.sh

external-env-validate:
	infra/scripts/external-env-validate.sh

soak-check:
	infra/scripts/soak-check.py

mobile-qa-checklist:
	infra/scripts/mobile-qa-checklist.sh

route-smoke:
	infra/scripts/web-route-smoke.sh

cli-doctor:
	cd apps/api && uv run coremcp doctor --api-url "$(API_URL)"

cli-service-list:
	@TOKEN="$${ADMIN_TOKEN:-$${COREMCP_ADMIN_TOKEN_VALUE:-$$(cat "$(ADMIN_TOKEN_FILE)" 2>/dev/null)}}"; cd apps/api && uv run coremcp service list --api-url "$(API_URL)" --token "$$TOKEN"

cli-token-issue:
	@TOKEN="$${ADMIN_TOKEN:-$${COREMCP_ADMIN_TOKEN_VALUE:-$$(cat "$(ADMIN_TOKEN_FILE)" 2>/dev/null)}}"; cd apps/api && uv run coremcp token issue --api-url "$(API_URL)" --token "$$TOKEN" --client-name "$(CLIENT_NAME)"

cli-backup-export:
	cd apps/api && uv run coremcp export --to "$(BACKUP_ARCHIVE)" --db "$(COREMCP_DB_PATH)" --secrets-file "$(COREMCP_SECRETS_FILE)" --fernet-key-file "$(FERNET_KEY_FILE)" --admin-token-file "$(ADMIN_TOKEN_FILE)"

cli-backup-import-dry-run:
	cd apps/api && uv run coremcp import --from "$(BACKUP_ARCHIVE)" --dry-run

backup-restore-drill:
	infra/scripts/backup-restore-drill.sh

redis-smoke:
	infra/scripts/redis-smoke.sh

tailscale-acl-validate:
	infra/scripts/tailscale-acl-validate.sh

project-docs-register:
	infra/scripts/register-project-docs-mcp.sh

git-workspace-register:
	infra/scripts/register-git-workspace-mcp.sh

codex-install:
	infra/scripts/codex-mcp-install.sh --force

codex-smoke:
	infra/scripts/codex-mcp-smoke.sh

codex-exec:
	infra/scripts/codex-exec-coremcp.sh

COREMCP_UI_SMOKE_WEB_URL ?= http://localhost:3003
COREMCP_UI_SMOKE_API_URL ?= $(API_URL)
COREMCP_UI_SMOKE_OUT_DIR ?= dev-plan/.artifacts/ui-smoke

.PHONY: ui-smoke-install ui-smoke ui-smoke-p0

ui-smoke-install:
	cd apps/api && uv run python -m playwright install chromium

ui-smoke:
	@curl -fsS "$(COREMCP_UI_SMOKE_API_URL)/health" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"'
	@curl -fsS "$(COREMCP_UI_SMOKE_API_URL)/ready" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ready"'
	@curl -fsSL -o /dev/null "$(COREMCP_UI_SMOKE_WEB_URL)"
	@cd apps/api && COREMCP_UI_SMOKE_WEB_URL="$(COREMCP_UI_SMOKE_WEB_URL)" COREMCP_UI_SMOKE_API_URL="$(COREMCP_UI_SMOKE_API_URL)" COREMCP_ADMIN_TOKEN_FILE="$(ADMIN_TOKEN_FILE)" COREMCP_UI_SMOKE_OUT_DIR="$(COREMCP_UI_SMOKE_OUT_DIR)" uv run python ../../infra/scripts/ui-smoke.py

# Extended P0 verification — coremcp-docs/test-checklist.md §13 자동화 가능 항목 일괄 실행
# (Health, Services S-01/02/04/05, Playground P-01 + read-only batch, Clients C-01,
#  Settings ST-01, Logs L-01/02, NF-01/02/04/06/09, Web UI D-01/02/04/06/07, E2E-D)
ui-smoke-p0:
	@curl -fsS "$(COREMCP_UI_SMOKE_API_URL)/health" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"'
	@cd apps/api && COREMCP_UI_SMOKE_WEB_URL="$(COREMCP_UI_SMOKE_WEB_URL)" COREMCP_UI_SMOKE_API_URL="$(COREMCP_UI_SMOKE_API_URL)" COREMCP_ADMIN_TOKEN_FILE="$(ADMIN_TOKEN_FILE)" COREMCP_UI_SMOKE_OUT_DIR="$(COREMCP_UI_SMOKE_OUT_DIR)" uv run python ../../infra/scripts/ui-smoke-p0.py
