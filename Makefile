.PHONY: test test-api test-fake lint build smoke

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
