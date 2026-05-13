.PHONY: bootstrap run run-local stop status test test-api test-fake lint build smoke ops-smoke route-smoke codex-install codex-smoke codex-exec

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

test: test-api test-fake

test-api:
	cd apps/api && uv run pytest

test-fake:
	cd apps/fake-mcp && uv run pytest

lint:
	pnpm lint

build:
	pnpm build

smoke:
	cd apps/api && uv run python -m coremcp.smoke

ops-smoke:
	infra/scripts/ops-smoke.sh

route-smoke:
	infra/scripts/web-route-smoke.sh

codex-install:
	infra/scripts/codex-mcp-install.sh --force

codex-smoke:
	infra/scripts/codex-mcp-smoke.sh

codex-exec:
	infra/scripts/codex-exec-coremcp.sh
