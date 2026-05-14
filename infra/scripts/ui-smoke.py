#!/usr/bin/env python3
"""CoreMCP Web Admin UI smoke test.

This script verifies the local personal CoreMCP gateway UI against a running API
and Web app. It intentionally avoids persisting raw tool arguments/results:
Playground screenshots redact those regions and events.json stores only counts,
flags, and metadata.
"""

from __future__ import annotations

import json
import os
import re
import traceback
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_WEB_URL = "http://localhost:3003"
DEFAULT_API_URL = "http://127.0.0.1:8787"
DEFAULT_TOKEN_FILE = "~/.coremcp/admin-token"
DEFAULT_OUT_DIR = "dev-plan/.artifacts/ui-smoke"

EXIT_DASHBOARD_LOAD_FAILED = 10
EXIT_SERVICES_EMPTY = 11
EXIT_PLAYGROUND_TOOLS_EMPTY = 12
EXIT_TOOL_CALL_ERROR = 13
EXIT_LOGS_CALL_MISSING = 14
EXIT_ENV_NOT_READY = 20

HTTP_TIMEOUT_SECONDS = 8
UI_TIMEOUT_MS = 20_000

SAFE_TOOL_PREFERENCES = (
    "demo_ops.ops_status",
    "demo_knowledge.note_search",
    "demo_bookmarks.bookmark_search",
    "demo_tasks.task_list",
)
SAFE_NAME_HINTS = (
    "status",
    "health",
    "list",
    "search",
    "read",
    "get",
    "find",
    "lookup",
    "summary",
)
UNSAFE_NAME_HINTS = (
    "delete",
    "remove",
    "write",
    "create",
    "update",
    "send",
    "purchase",
    "danger",
    "exec",
    "run",
    "shell",
    "command",
    "deploy",
    "rotate",
    "revoke",
    "reset",
)


class SmokeExit(Exception):
    """Expected smoke-test failure with a stable exit code."""

    def __init__(self, code: int, reason: str, **fields: Any) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.fields = fields


@dataclass
class Config:
    repo_root: Path
    web_url: str
    api_url: str
    token_file: Path
    out_dir: Path
    screenshots_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        repo_root = Path(__file__).resolve().parents[2]
        web_url = os.environ.get("COREMCP_UI_SMOKE_WEB_URL", DEFAULT_WEB_URL).rstrip("/")
        api_url = os.environ.get("COREMCP_UI_SMOKE_API_URL", DEFAULT_API_URL).rstrip("/")
        token_file = Path(os.environ.get("COREMCP_ADMIN_TOKEN_FILE", DEFAULT_TOKEN_FILE)).expanduser()
        raw_out_dir = Path(os.environ.get("COREMCP_UI_SMOKE_OUT_DIR", DEFAULT_OUT_DIR)).expanduser()
        out_dir = raw_out_dir if raw_out_dir.is_absolute() else repo_root / raw_out_dir
        screenshots_dir = out_dir / "screenshots"
        return cls(
            repo_root=repo_root,
            web_url=web_url,
            api_url=api_url,
            token_file=token_file,
            out_dir=out_dir,
            screenshots_dir=screenshots_dir,
        )


@dataclass
class SmokeContext:
    config: Config
    events: list[dict[str, Any]] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: now_iso())
    admin_token: str = ""
    selected_tool: str = ""

    def log(self, event: str, **fields: Any) -> None:
        record = {"ts": now_iso(), "event": event, **fields}
        self.events.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def ensure_dirs(config: Config) -> None:
    config.out_dir.mkdir(parents=True, exist_ok=True)
    config.screenshots_dir.mkdir(parents=True, exist_ok=True)


def join_url(base: str, path: str) -> str:
    return urllib.parse.urljoin(f"{base.rstrip('/')}/", path.lstrip("/"))


def short_body(body: bytes, max_chars: int = 300) -> str:
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:
        text = repr(body[:max_chars])
    return text[:max_chars]


def http_request_json(
    ctx: SmokeContext,
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = join_url(ctx.config.api_url, path)
    headers = {"Accept": "application/json"}
    data: bytes | None = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read()
            if response.status >= 400:
                raise RuntimeError(f"HTTP {response.status}: {short_body(raw)}")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        raise RuntimeError(f"HTTP {exc.code}: {short_body(raw)}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc

    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON from {path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"expected JSON object from {path}")
    return parsed


def http_check_web(ctx: SmokeContext) -> None:
    request = urllib.request.Request(ctx.config.web_url, headers={"Accept": "text/html,application/xhtml+xml"})
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            body = response.read(512)
            status = response.status
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {short_body(exc.read())}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc
    if status >= 400:
        raise RuntimeError(f"HTTP {status}: {short_body(body)}")


def read_admin_token(ctx: SmokeContext) -> str:
    token_file = ctx.config.token_file
    if not token_file.exists():
        raise SmokeExit(EXIT_ENV_NOT_READY, "admin token file is missing", token_file=str(token_file))
    token = token_file.read_text(encoding="utf-8").strip()
    if not token:
        raise SmokeExit(EXIT_ENV_NOT_READY, "admin token file is empty", token_file=str(token_file))
    ctx.log("admin_token_read", token_file=str(token_file), token_length=len(token))
    return token


def preflight(ctx: SmokeContext) -> None:
    ctx.log(
        "preflight_start",
        web_url=ctx.config.web_url,
        api_url=ctx.config.api_url,
        out_dir=str(ctx.config.out_dir),
    )
    ctx.admin_token = read_admin_token(ctx)

    try:
        health = http_request_json(ctx, "GET", "/health")
        ctx.log("api_health", ok=health.get("status") == "ok", status=health.get("status"))
        if health.get("status") != "ok":
            raise SmokeExit(EXIT_ENV_NOT_READY, "API /health is not ok", status=health.get("status"))

        ready = http_request_json(ctx, "GET", "/ready")
        ctx.log("api_ready", ok=ready.get("status") == "ready", status=ready.get("status"))
        if ready.get("status") != "ready":
            raise SmokeExit(EXIT_ENV_NOT_READY, "API /ready is not ready", status=ready.get("status"))

        settings = http_request_json(ctx, "GET", "/v1/settings", token=ctx.admin_token)
        ctx.log(
            "api_admin_auth",
            ok=True,
            auth_mode=settings.get("auth_mode"),
            app_version=settings.get("app_version"),
            client_token_count=settings.get("client_token_count"),
        )

        http_check_web(ctx)
        ctx.log("web_ready", ok=True, url=ctx.config.web_url)
    except SmokeExit:
        raise
    except Exception as exc:
        raise SmokeExit(EXIT_ENV_NOT_READY, "preflight failed", error=str(exc), type=type(exc).__name__) from exc


def list_items(ctx: SmokeContext, path: str) -> list[dict[str, Any]]:
    payload = http_request_json(ctx, "GET", path, token=ctx.admin_token)
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def tool_schema(tool: dict[str, Any]) -> dict[str, Any]:
    schema = tool.get("inputSchema") or tool.get("input_schema") or {}
    return schema if isinstance(schema, dict) else {}


def required_fields(schema: dict[str, Any]) -> list[str]:
    required = schema.get("required")
    return [item for item in required if isinstance(item, str)] if isinstance(required, list) else []


def tool_is_safe(tool: dict[str, Any]) -> bool:
    name = str(tool.get("name") or "").lower()
    if not name:
        return False
    if any(hint in name for hint in UNSAFE_NAME_HINTS):
        return False
    if name in SAFE_TOOL_PREFERENCES:
        return True
    if any(hint in name for hint in SAFE_NAME_HINTS):
        return True
    return not required_fields(tool_schema(tool))


def generate_value(property_schema: dict[str, Any], field_name: str) -> Any:
    enum_values = property_schema.get("enum")
    if isinstance(enum_values, list) and enum_values:
        return enum_values[0]
    raw_type = property_schema.get("type")
    if isinstance(raw_type, list):
        raw_type = next((item for item in raw_type if item != "null"), raw_type[0] if raw_type else "string")
    if raw_type in {"integer", "number"}:
        return 1
    if raw_type == "boolean":
        return True
    if raw_type == "array":
        return []
    if raw_type == "object":
        return {}
    lowered = field_name.lower()
    if "query" in lowered or "search" in lowered:
        return "ops"
    if "message" in lowered or "text" in lowered:
        return "hello"
    return "smoke"


def generate_args(tool: dict[str, Any]) -> dict[str, Any]:
    name = str(tool.get("name") or "")
    if name == "demo_knowledge.note_search":
        return {"query": "ops"}
    if name == "demo_bookmarks.bookmark_search":
        return {"query": "ops"}
    schema = tool_schema(tool)
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    args: dict[str, Any] = {}
    for field_name in required_fields(schema):
        raw_property = properties.get(field_name)
        property_schema = raw_property if isinstance(raw_property, dict) else {}
        args[field_name] = generate_value(property_schema, field_name)
    return args


def choose_tool(tools: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    by_name = {str(tool.get("name") or ""): tool for tool in tools}
    for preferred in SAFE_TOOL_PREFERENCES:
        if preferred in by_name:
            tool = by_name[preferred]
            return tool, generate_args(tool)

    safe_tools = [tool for tool in tools if tool_is_safe(tool)]
    no_required = [tool for tool in safe_tools if not required_fields(tool_schema(tool))]
    candidates = no_required or safe_tools
    if not candidates:
        raise SmokeExit(
            EXIT_ENV_NOT_READY,
            "no safe read-only-looking tool available for smoke call",
            tool_count=len(tools),
        )

    def score(tool: dict[str, Any]) -> tuple[int, str]:
        name = str(tool.get("name") or "").lower()
        hint_score = 0
        for index, hint in enumerate(SAFE_NAME_HINTS):
            if hint in name:
                hint_score = index + 1
                break
        return (hint_score or 99, name)

    chosen = sorted(candidates, key=score)[0]
    return chosen, generate_args(chosen)


def parse_services_count(text: str) -> int | None:
    match = re.search(r"전체\s+(\d+)개", text)
    if match:
        return int(match.group(1))
    match = re.search(r"표시\s+\d+개\s*/\s*전체\s+(\d+)개", text)
    if match:
        return int(match.group(1))
    return None


def result_error_state(result_text: str) -> tuple[bool, str]:
    try:
        parsed = json.loads(result_text)
    except json.JSONDecodeError:
        if re.search(r'"isError"\s*:\s*true', result_text):
            return True, "isError_true_text"
        if "오류" in result_text or "error" in result_text.lower():
            return True, "non_json_error_text"
        return False, "non_json"

    if not isinstance(parsed, dict):
        return False, type(parsed).__name__
    if parsed.get("isError") is True:
        return True, "top_level_isError_true"
    result = parsed.get("result")
    if isinstance(result, dict) and result.get("isError") is True:
        return True, "result_isError_true"
    if parsed.get("error") is not None:
        return True, "jsonrpc_error"
    return False, "json_ok"


def redact_playground_for_screenshot(page: Any) -> None:
    page.evaluate(
        """
        () => {
          const redacted = '[redacted by ui-smoke: tool arguments/results are not stored]';
          const textarea = document.querySelector('textarea#arguments');
          if (textarea) textarea.value = JSON.stringify({ redacted: true }, null, 2);
          document.querySelectorAll('pre.cm-code-block').forEach((node) => {
            node.textContent = redacted;
          });
        }
        """
    )


def screenshot(ctx: SmokeContext, page: Any, name: str, *, redact_playground: bool = False) -> None:
    if redact_playground:
        redact_playground_for_screenshot(page)
    path = ctx.config.screenshots_dir / f"{len(ctx.screenshots) + 1:02d}_{name}.png"
    page.screenshot(path=str(path), full_page=True)
    rel_path = str(path.relative_to(ctx.config.repo_root)) if path.is_relative_to(ctx.config.repo_root) else str(path)
    ctx.screenshots.append(rel_path)
    ctx.log("screenshot", name=name, path=rel_path, redacted=redact_playground)


def wait_for_latest_status(page: Any) -> bool:
    try:
        page.locator(".cm-status-banner").wait_for(state="visible", timeout=UI_TIMEOUT_MS)
        page.wait_for_function(
            """
            () => Array.from(document.querySelectorAll('.cm-status-banner'))
              .some((node) => (node.textContent || '').includes('최신 데이터를 불러왔습니다.'))
            """,
            timeout=UI_TIMEOUT_MS,
        )
        return True
    except Exception:
        return False


def status_banner_text(page: Any) -> str:
    try:
        return page.locator(".cm-status-banner").first.inner_text(timeout=2_000)
    except Exception:
        return ""


def goto_section(ctx: SmokeContext, page: Any, path: str, selector: str, shot_name: str, *, latest: bool = False) -> None:
    url = f"{ctx.config.web_url}{path}"
    page.goto(url, wait_until="domcontentloaded", timeout=UI_TIMEOUT_MS)
    page.locator(selector).wait_for(state="visible", timeout=UI_TIMEOUT_MS)
    if latest and not wait_for_latest_status(page):
        screenshot(ctx, page, f"{shot_name}_load_failed")
        raise SmokeExit(
            EXIT_DASHBOARD_LOAD_FAILED,
            "dashboard latest data load failed",
            path=path,
            status_banner=status_banner_text(page),
        )
    screenshot(ctx, page, shot_name)


def run_browser(ctx: SmokeContext) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise SmokeExit(
            EXIT_ENV_NOT_READY,
            "playwright is not installed; run make ui-smoke-install",
            missing_module=str(exc),
        ) from exc

    try:
        services_api = list_items(ctx, "/v1/mcp-services?limit=100")
        ctx.log("api_services", count=len(services_api))
    except Exception as exc:
        raise SmokeExit(
            EXIT_DASHBOARD_LOAD_FAILED,
            "admin services data prefetch failed",
            error=str(exc),
            type=type(exc).__name__,
        ) from exc

    try:
        tools_api = list_items(ctx, "/v1/playground/tools/list?limit=100")
        ctx.log("api_playground_tools", count=len(tools_api))
    except Exception as exc:
        tools_api = []
        ctx.log("api_playground_tools", ok=False, count=0, error=str(exc), type=type(exc).__name__)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1024}, locale="ko-KR")
        context.add_init_script(
            f"window.sessionStorage.setItem('coremcp_admin_token', {json.dumps(ctx.admin_token)});"
        )
        page = context.new_page()
        page.on("console", lambda message: ctx.log("browser_console", level=message.type, text_bytes=len(message.text.encode("utf-8"))))
        page.on("pageerror", lambda error: ctx.log("browser_pageerror", error_type=type(error).__name__))
        page.on("requestfailed", lambda request: ctx.log("browser_request_failed", url=request.url, failure=request.failure))

        try:
            goto_section(ctx, page, "/", "#dashboard", "dashboard", latest=True)

            goto_section(ctx, page, "/services", "article#services", "services")
            services_text = page.locator("article#services").inner_text(timeout=5_000)
            ui_service_count = parse_services_count(services_text)
            if ui_service_count is None:
                ui_service_count = len(services_api)
            ctx.log("ui_services", count=ui_service_count, api_count=len(services_api))
            if ui_service_count == 0:
                raise SmokeExit(EXIT_SERVICES_EMPTY, "services page has zero services", api_count=len(services_api))

            page.goto(f"{ctx.config.web_url}/playground", wait_until="domcontentloaded", timeout=UI_TIMEOUT_MS)
            page.locator("section#playground").wait_for(state="visible", timeout=UI_TIMEOUT_MS)
            page.get_by_role("button", name="도구 목록 불러오기").click(timeout=UI_TIMEOUT_MS)
            try:
                page.wait_for_function(
                    "() => Array.from(document.querySelectorAll('select#tool-select option')).some((option) => option.value)",
                    timeout=UI_TIMEOUT_MS,
                )
            except Exception:
                pass
            option_values = page.locator("select#tool-select option").evaluate_all(
                "options => options.map((option) => option.value).filter(Boolean)"
            )
            tool_count = len(option_values)
            ctx.log("ui_playground_tools", count=tool_count, sample=option_values[:5])
            if tool_count == 0:
                screenshot(ctx, page, "playground_tools_empty", redact_playground=True)
                raise SmokeExit(EXIT_PLAYGROUND_TOOLS_EMPTY, "playground has zero tools", api_count=len(tools_api))

            tools_for_selection = tools_api or [{"name": value} for value in option_values]
            chosen_tool, args = choose_tool(tools_for_selection)
            chosen_name = str(chosen_tool.get("name") or option_values[0])
            if chosen_name not in option_values:
                chosen_name = option_values[0]
                chosen_tool = next((tool for tool in tools_for_selection if tool.get("name") == chosen_name), {"name": chosen_name})
                args = generate_args(chosen_tool)
            ctx.selected_tool = chosen_name
            ctx.log("tool_selected", name=chosen_name, arg_keys=sorted(args.keys()))

            page.select_option("select#tool-select", chosen_name)
            json_button = page.get_by_role("button", name="JSON 직접 편집")
            try:
                if json_button.is_visible(timeout=1_000):
                    json_button.click(timeout=3_000)
            except Exception:
                pass
            page.locator("textarea#arguments").fill(json.dumps(args, ensure_ascii=False, indent=2), timeout=UI_TIMEOUT_MS)
            page.get_by_role("button", name="Call tool").click(timeout=UI_TIMEOUT_MS)
            page.wait_for_function(
                """
                () => {
                  const pre = document.querySelector('pre.cm-code-block');
                  const text = pre ? pre.textContent || '' : '';
                  return text && !text.includes('도구를 호출하는 중입니다');
                }
                """,
                timeout=UI_TIMEOUT_MS,
            )
            result_text = page.locator("pre.cm-code-block").first.inner_text(timeout=5_000)
            is_error, result_shape = result_error_state(result_text)
            ctx.log(
                "tool_call_result",
                tool=chosen_name,
                is_error=is_error,
                result_shape=result_shape,
                result_bytes=len(result_text.encode("utf-8")),
            )
            screenshot(ctx, page, "playground_call", redact_playground=True)
            if is_error:
                raise SmokeExit(EXIT_TOOL_CALL_ERROR, "tool call returned an error", tool=chosen_name, result_shape=result_shape)

            page.goto(f"{ctx.config.web_url}/logs", wait_until="domcontentloaded", timeout=UI_TIMEOUT_MS)
            page.locator("section#logs").wait_for(state="visible", timeout=UI_TIMEOUT_MS)
            try:
                page.wait_for_function(
                    "toolName => document.body && (document.body.textContent || '').includes(toolName)",
                    arg=chosen_name,
                    timeout=UI_TIMEOUT_MS,
                )
            except Exception:
                try:
                    page.get_by_role("button", name="새로고침").click(timeout=3_000)
                    page.wait_for_function(
                        "toolName => document.body && (document.body.textContent || '').includes(toolName)",
                        arg=chosen_name,
                        timeout=8_000,
                    )
                except Exception:
                    pass
            logs_text = page.locator("section#logs").inner_text(timeout=5_000)
            has_call = chosen_name in logs_text
            ctx.log("ui_logs", tool=chosen_name, has_recent_call=has_call)
            screenshot(ctx, page, "logs")
            if not has_call:
                raise SmokeExit(EXIT_LOGS_CALL_MISSING, "logs page does not show the most recent tool call", tool=chosen_name)
        finally:
            browser.close()


def write_events(ctx: SmokeContext, exit_code: int, reason: str | None = None) -> None:
    ensure_dirs(ctx.config)
    payload = {
        "metadata": {
            "started_at": ctx.started_at,
            "finished_at": now_iso(),
            "web_url": ctx.config.web_url,
            "api_url": ctx.config.api_url,
            "admin_token_file": str(ctx.config.token_file),
            "out_dir": str(ctx.config.out_dir),
        },
        "summary": {
            "exit_code": exit_code,
            "reason": reason,
            "selected_tool": ctx.selected_tool or None,
            "screenshots": ctx.screenshots,
        },
        "events": ctx.events,
    }
    path = ctx.config.out_dir / "events.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"EVENTS: {path}", flush=True)
    print(f"SCREENSHOTS: {ctx.config.screenshots_dir}", flush=True)


def main() -> int:
    config = Config.from_env()
    ensure_dirs(config)
    ctx = SmokeContext(config=config)
    exit_code = 1
    reason: str | None = None
    try:
        preflight(ctx)
        run_browser(ctx)
        exit_code = 0
        reason = "ok"
        ctx.log("smoke_passed", exit_code=exit_code)
    except SmokeExit as exc:
        exit_code = exc.code
        reason = exc.reason
        ctx.log("smoke_failed", exit_code=exc.code, reason=exc.reason, **exc.fields)
    except Exception as exc:  # Unexpected implementation/runtime failure.
        exit_code = 1
        reason = f"unexpected {type(exc).__name__}: {exc}"
        ctx.log("fatal", error=str(exc), type=type(exc).__name__, traceback=traceback.format_exc(limit=8))
    finally:
        write_events(ctx, exit_code, reason)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
