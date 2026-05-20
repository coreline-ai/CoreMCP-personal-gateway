from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from coremcp.api.simulator import extract_tool_calls, run_codex_simulator
from coremcp.main import create_app
from coremcp.settings import Settings

TOKEN = "cmcp_admin_testtoken"


def auth_headers(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_extract_tool_calls_from_codex_output() -> None:
    output = """
mcp: coremcp/project_docs.project_docs_search started
mcp: coremcp/project_docs.project_docs_search (completed)
"""

    assert extract_tool_calls(output) == [
        {"server": "coremcp", "name": "project_docs.project_docs_search", "status": "started"},
        {"server": "coremcp", "name": "project_docs.project_docs_search", "status": "completed"},
    ]


@pytest.mark.asyncio
async def test_run_codex_simulator_success_with_script(tmp_path: Path) -> None:
    script = tmp_path / "codex-ok.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "echo \"mcp: coremcp/project_docs.project_list started\"\n"
        "echo \"mcp: coremcp/project_docs.project_list (completed)\"\n"
        "echo \"done: $1\"\n",
        encoding="utf-8",
    )
    script.chmod(0o755)

    result = await run_codex_simulator(
        prompt="hello",
        timeout_seconds=5,
        settings=Settings(COREMCP_CODEX_SIMULATOR_SCRIPT=script),
    )

    assert result["status"] == "completed"
    assert result["exit_code"] == 0
    assert result["tool_calls"][-1] == {"server": "coremcp", "name": "project_docs.project_list", "status": "completed"}
    assert "done: hello" in result["answer"]


@pytest.mark.asyncio
async def test_run_codex_simulator_timeout_kills_process(tmp_path: Path) -> None:
    script = tmp_path / "codex-sleep.sh"
    script.write_text("#!/usr/bin/env bash\nsleep 5\n", encoding="utf-8")
    script.chmod(0o755)

    result = await run_codex_simulator(
        prompt="slow",
        timeout_seconds=1,
        settings=Settings(COREMCP_CODEX_SIMULATOR_SCRIPT=script),
    )

    assert result["status"] == "timed_out"
    assert result["exit_code"] is not None


@pytest.mark.asyncio
async def test_codex_simulator_route_requires_admin_token(tmp_path: Path) -> None:
    app = create_app(
        settings=Settings(
            COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
            COREMCP_ADMIN_TOKEN_FILE=tmp_path / "admin-token",
            COREMCP_DB_PATH=tmp_path / "simulator-auth.sqlite3",
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=tmp_path / "secrets.json",
        )
    )

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.post("/v1/simulator/codex/run", json={"prompt": "hi"})

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_codex_simulator_route_uses_runner_and_audits(tmp_path: Path) -> None:
    async def fake_runner(*, prompt: str, timeout_seconds: int, settings: Settings) -> dict[str, Any]:
        return {
            "status": "completed",
            "exit_code": 0,
            "duration_ms": 12,
            "answer": f"answer: {prompt}",
            "stdout": "mcp: coremcp/project_docs.project_list (completed)\nanswer",
            "stderr": "",
            "tool_calls": [{"server": "coremcp", "name": "project_docs.project_list", "status": "completed"}],
            "stdout_truncated": False,
            "stderr_truncated": False,
        }

    app = create_app(
        settings=Settings(
            COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
            COREMCP_ADMIN_TOKEN_FILE=tmp_path / "admin-token",
            COREMCP_DB_PATH=tmp_path / "simulator.sqlite3",
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=tmp_path / "secrets.json",
        )
    )

    async with app.router.lifespan_context(app):
        app.state.codex_simulator_runner = fake_runner
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.post(
                "/v1/simulator/codex/run",
                headers=auth_headers(),
                json={"prompt": "project list", "timeout_seconds": 10},
            )
        audit = await app.state.repository.recent_audit_logs(limit=1, action="simulator.codex.run")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["tool_calls"][0]["name"] == "project_docs.project_list"
    assert audit[0]["metadata"]["prompt_length"] == len("project list")
    assert "project list" not in str(audit[0]["metadata"])


@pytest.mark.asyncio
async def test_codex_simulator_route_rejects_too_long_prompt(tmp_path: Path) -> None:
    app = create_app(
        settings=Settings(
            COREMCP_ADMIN_TOKEN_VALUE=TOKEN,
            COREMCP_ADMIN_TOKEN_FILE=tmp_path / "admin-token",
            COREMCP_DB_PATH=tmp_path / "simulator-validation.sqlite3",
            COREMCP_SECRET_BACKEND="fernet",
            COREMCP_SECRETS_FILE=tmp_path / "secrets.json",
            COREMCP_CODEX_SIMULATOR_MAX_PROMPT_CHARS=4,
        )
    )

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.post(
                "/v1/simulator/codex/run",
                headers=auth_headers(),
                json={"prompt": "too long"},
            )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
