from __future__ import annotations

import asyncio
import os
import re
import signal
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from coremcp.api._schemas import CodexSimulatorResponse
from coremcp.settings import Settings

SENSITIVE_ENV_KEYS = {
    "authorization",
    "coremcp_admin_token",
    "coremcp_admin_token_value",
}
TOOL_CALL_RE = re.compile(
    r"mcp:\s*(?P<server>[A-Za-z0-9_.-]+)/(?P<tool>[A-Za-z0-9_.-]+)(?:\s+(?P<status>started|\(completed\)|\(failed\)|completed|failed))?",
    re.IGNORECASE,
)
MAX_OUTPUT_CHARS = 120_000


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _resolve_script(settings: Settings) -> Path:
    script = settings.codex_simulator_script.expanduser()
    if not script.is_absolute():
        script = _repo_root() / script
    return script


def _safe_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in list(env):
        if key.lower() in SENSITIVE_ENV_KEYS:
            env.pop(key, None)
    home = Path.home()
    env.setdefault("HOME", str(home))
    env.setdefault("USER", home.name)
    env.setdefault("SHELL", "/bin/zsh")
    env.setdefault("CODEX_HOME", str(home / ".codex"))
    stable_paths = [
        *(str(path) for path in sorted((home / ".nvm" / "versions" / "node").glob("*/bin"), reverse=True)),
        str(home / ".bun" / "bin"),
        str(home / "Library" / "pnpm"),
        "/opt/homebrew/bin",
        "/opt/homebrew/sbin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    existing_path = env.get("PATH", "")
    env["PATH"] = ":".join([*stable_paths, existing_path] if existing_path else stable_paths)
    return env


def extract_tool_calls(output: str) -> list[dict[str, str | None]]:
    events: list[dict[str, str | None]] = []
    for match in TOOL_CALL_RE.finditer(output):
        raw_status = match.group("status") or "observed"
        status = raw_status.strip("()").lower()
        events.append(
            {
                "server": match.group("server"),
                "name": match.group("tool"),
                "status": status,
            }
        )
    return events


def _truncate_output(value: str, max_chars: int = MAX_OUTPUT_CHARS) -> tuple[str, bool]:
    if len(value) <= max_chars:
        return value, False
    return value[:max_chars], True


def _extract_answer(stdout: str) -> str:
    lines = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append(line)
            continue
        if " WARN " in stripped or stripped.startswith("tokens used"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()[-20_000:]


async def run_codex_simulator(
    *,
    prompt: str,
    timeout_seconds: int,
    settings: Settings,
) -> dict[str, Any]:
    started = time.monotonic()
    script = _resolve_script(settings)
    if not script.exists():
        return {
            "status": "failed",
            "exit_code": 66,
            "duration_ms": 0,
            "answer": "",
            "stdout": "",
            "stderr": f"Codex simulator script not found: {script}",
            "tool_calls": [],
            "stdout_truncated": False,
            "stderr_truncated": False,
        }

    try:
        process = await asyncio.create_subprocess_exec(
            str(script),
            prompt,
            cwd=str(_repo_root()),
            env=_safe_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        return {
            "status": "failed",
            "exit_code": 69,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "answer": "",
            "stdout": "",
            "stderr": str(exc),
            "tool_calls": [],
            "stdout_truncated": False,
            "stderr_truncated": False,
        }
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        timed_out = False
    except TimeoutError:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=5)

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    stdout, stdout_truncated = _truncate_output(stdout)
    stderr, stderr_truncated = _truncate_output(stderr)
    duration_ms = int((time.monotonic() - started) * 1000)
    exit_code = process.returncode
    status = "timed_out" if timed_out else ("completed" if exit_code == 0 else "failed")
    combined = f"{stdout}\n{stderr}"
    return {
        "status": status,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "answer": _extract_answer(stdout),
        "stdout": stdout,
        "stderr": stderr,
        "tool_calls": extract_tool_calls(combined),
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    }


def register_simulator_routes(
    app: FastAPI,
    *,
    verify_admin_request: Callable[[Request], bool],
    unauthorized_response: Callable[..., JSONResponse],
    json_body: Callable[[Request], Awaitable[dict[str, Any] | JSONResponse]],
    api_error: Callable[..., JSONResponse],
    request_ip: Callable[[Request], str | None],
    correlation_id: Callable[[Request], str],
) -> None:
    @app.post("/v1/simulator/codex/run", response_model=CodexSimulatorResponse)
    async def run_codex_simulator_route(request: Request) -> Response:
        if not verify_admin_request(request):
            return unauthorized_response()
        settings: Settings = request.app.state.settings
        if not settings.codex_simulator_enabled:
            return api_error("simulator_disabled", "Codex simulator is disabled.", status_code=403)

        body = await json_body(request)
        if isinstance(body, JSONResponse):
            return body
        prompt = body.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return api_error("validation_failed", "prompt is required", status_code=422)
        prompt = prompt.strip()
        if len(prompt) > settings.codex_simulator_max_prompt_chars:
            return api_error(
                "validation_failed",
                f"prompt exceeds {settings.codex_simulator_max_prompt_chars} characters",
                status_code=422,
            )
        requested_timeout = body.get("timeout_seconds", settings.codex_simulator_timeout_seconds)
        if isinstance(requested_timeout, bool) or not isinstance(requested_timeout, int):
            return api_error("validation_failed", "timeout_seconds must be an integer", status_code=422)
        timeout_seconds = max(1, min(requested_timeout, settings.codex_simulator_timeout_seconds))

        runner = getattr(request.app.state, "codex_simulator_runner", run_codex_simulator)
        result = await runner(prompt=prompt, timeout_seconds=timeout_seconds, settings=settings)
        await request.app.state.repos.audit.log_audit(
            action="simulator.codex.run",
            resource_type="simulator",
            resource_id="codex",
            metadata={
                "status": result.get("status"),
                "exit_code": result.get("exit_code"),
                "duration_ms": result.get("duration_ms"),
                "prompt_length": len(prompt),
                "tool_call_count": len(result.get("tool_calls") or []),
            },
            request_id=correlation_id(request),
            ip=request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        return JSONResponse(result)
