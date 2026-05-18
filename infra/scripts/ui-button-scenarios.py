#!/usr/bin/env python3
"""CoreMCP Web Admin button/interaction scenario suite.

Runs 100 safe UI scenarios across every admin screen. The suite uses a
throwaway service/connection for mutating checks and cleans them up through the
API. It intentionally avoids storing raw secrets or raw tool outputs in the
artifact log.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, expect, sync_playwright

DEFAULT_WEB_URL = "http://localhost:3003"
DEFAULT_API_URL = "http://127.0.0.1:8787"
DEFAULT_TOKEN_FILE = "~/.coremcp/admin-token"
DEFAULT_OUT_DIR = "dev-plan/.artifacts/button-scenarios"
UI_TIMEOUT_MS = 12_000


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
        web_url = os.environ.get("COREMCP_UI_SCENARIO_WEB_URL", os.environ.get("COREMCP_UI_SMOKE_WEB_URL", DEFAULT_WEB_URL)).rstrip("/")
        api_url = os.environ.get("COREMCP_UI_SCENARIO_API_URL", os.environ.get("COREMCP_UI_SMOKE_API_URL", DEFAULT_API_URL)).rstrip("/")
        token_file = Path(os.environ.get("COREMCP_ADMIN_TOKEN_FILE", DEFAULT_TOKEN_FILE)).expanduser()
        raw_out = Path(os.environ.get("COREMCP_UI_SCENARIO_OUT_DIR", DEFAULT_OUT_DIR)).expanduser()
        out_dir = raw_out if raw_out.is_absolute() else repo_root / raw_out
        return cls(repo_root, web_url, api_url, token_file, out_dir, out_dir / "screenshots")


@dataclass
class ScenarioContext:
    config: Config
    token: str
    results: list[dict[str, Any]] = field(default_factory=list)
    temp_service_id: str | None = None
    temp_service_slug: str = field(default_factory=lambda: f"qa_button_{int(time.time())}")
    temp_connection_id: str | None = None
    temp_client_name: str | None = None
    temp_token_id: str | None = None

    def record(self, case_id: str, name: str, status: str, detail: str = "") -> None:
        row = {"case": case_id, "name": name, "status": status, "detail": detail}
        self.results.append(row)
        icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️"}.get(status, "❓")
        print(f"{icon} {case_id:>4s} {status:5s} {name} {detail}", flush=True)


def ensure_dirs(config: Config) -> None:
    config.out_dir.mkdir(parents=True, exist_ok=True)
    config.screenshots_dir.mkdir(parents=True, exist_ok=True)


def join_url(base: str, path: str) -> str:
    return urllib.parse.urljoin(f"{base.rstrip('/')}/", path.lstrip("/"))


def api(ctx: ScenarioContext, method: str, path: str, body: dict[str, Any] | None = None, *, auth: bool = True) -> tuple[int, dict[str, Any]]:
    headers = {"Accept": "application/json"}
    data: bytes | None = None
    if auth:
        headers["Authorization"] = f"Bearer {ctx.token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(join_url(ctx.config.api_url, path), data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            raw = res.read().decode("utf-8")
            return res.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"error": raw[:300]}
        return exc.code, payload


def set_token(page: Page, token: str) -> None:
    page.evaluate("([k, v]) => sessionStorage.setItem(k, v)", ["coremcp_admin_token", token])


def goto(page: Page, ctx: ScenarioContext, path: str) -> None:
    page.goto(join_url(ctx.config.web_url, path), wait_until="networkidle", timeout=UI_TIMEOUT_MS)


def visible_text(page: Page, text: str) -> None:
    expect(page.get_by_text(text).first).to_be_visible(timeout=UI_TIMEOUT_MS)


def click_link(page: Page, name: str) -> None:
    page.get_by_role("link", name=name).first.click(timeout=UI_TIMEOUT_MS)
    page.wait_for_load_state("networkidle", timeout=UI_TIMEOUT_MS)


def click_button(page: Page, name: str) -> None:
    page.get_by_role("button", name=name).first.click(timeout=UI_TIMEOUT_MS)


def expect_path(page: Page, suffix: str) -> None:
    assert page.url.rstrip("/").endswith(suffix.rstrip("/")), f"expected path {suffix}, got {page.url}"


def add_case(cases: list[tuple[str, str, Callable[[], None]]], case_id: str, name: str, fn: Callable[[], None]) -> None:
    cases.append((case_id, name, fn))


def run_cases(ctx: ScenarioContext, cases: list[tuple[str, str, Callable[[], None]]]) -> None:
    for case_id, name, fn in cases:
        try:
            fn()
            ctx.record(case_id, name, "PASS")
        except Exception as exc:  # continue to find all failures
            ctx.record(case_id, name, "FAIL", f"{type(exc).__name__}: {exc}")


def find_temp_service(ctx: ScenarioContext) -> None:
    ctx.temp_service_id = None
    code, payload = api(ctx, "GET", "/v1/mcp-services?limit=100")
    if code != 200:
        return
    for item in payload.get("items", []):
        if item.get("slug") == ctx.temp_service_slug:
            ctx.temp_service_id = str(item["id"])
            return


def cleanup(ctx: ScenarioContext) -> None:
    if ctx.temp_connection_id:
        api(ctx, "DELETE", f"/v1/external-connections/{ctx.temp_connection_id}")
    find_temp_service(ctx)
    if ctx.temp_service_id:
        api(ctx, "DELETE", f"/v1/mcp-services/{ctx.temp_service_id}")


def main() -> int:
    config = Config.from_env()
    ensure_dirs(config)
    token = config.token_file.read_text(encoding="utf-8").strip()
    ctx = ScenarioContext(config=config, token=token)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        console_errors: list[str] = []
        request_failures: list[dict[str, str]] = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on(
            "requestfailed",
            lambda req: request_failures.append({"url": req.url, "error": (req.failure or "unknown")})
            if "_rsc=" not in req.url
            else None,
        )

        cases: list[tuple[str, str, Callable[[], None]]] = []

        # Admin shell / navigation / dashboard (1-20)
        add_case(cases, "S001", "API health returns ok", lambda: assert_api(ctx, "/health", "status", "ok", auth=False))
        add_case(cases, "S002", "API ready returns ready", lambda: assert_api(ctx, "/ready", "status", "ready", auth=False))
        add_case(cases, "S003", "Dashboard loads with token", lambda: load_with_token(page, ctx, "/", "Read-only overview"))
        add_case(cases, "S004", "Sidebar Services link navigates", lambda: nav_link(page, ctx, "/", "Services", "/services"))
        add_case(cases, "S005", "Sidebar Toolbox link navigates", lambda: nav_link(page, ctx, "/", "Toolbox", "/toolbox"))
        add_case(cases, "S006", "Sidebar Clients link navigates", lambda: nav_link(page, ctx, "/", "Clients", "/clients"))
        add_case(cases, "S007", "Sidebar Settings link navigates", lambda: nav_link(page, ctx, "/", "Settings", "/settings"))
        add_case(cases, "S008", "Sidebar Playground link navigates", lambda: nav_link(page, ctx, "/", "Playground", "/playground"))
        add_case(cases, "S009", "Sidebar Logs link navigates", lambda: nav_link(page, ctx, "/", "Logs", "/logs"))
        add_case(cases, "S010", "Home logo navigates dashboard", lambda: nav_link(page, ctx, "/services", "CoreMCP home", ""))
        add_case(cases, "S011", "Health button reports API status", lambda: health_button(page, ctx))
        add_case(cases, "S012", "Refresh button refreshes data", lambda: refresh_button(page, ctx))
        add_case(cases, "S013", "Token clear hides data", lambda: token_clear_restore(page, ctx))
        add_case(cases, "S014", "Dashboard MCP 추가/등록 link", lambda: nav_link(page, ctx, "/", "MCP 추가/등록", "/services"))
        add_case(cases, "S015", "Dashboard 도구 테스트 link", lambda: nav_link(page, ctx, "/", "도구 테스트", "/playground"))
        add_case(cases, "S016", "Dashboard 최근 로그 link", lambda: nav_link(page, ctx, "/", "최근 로그", "/logs"))
        add_case(cases, "S017", "Dashboard Default Toolbox card link", lambda: nav_link(page, ctx, "/", "Default Toolbox", "/toolbox"))
        add_case(cases, "S018", "Dashboard MCP Services card link", lambda: nav_link(page, ctx, "/", "MCP Services", "/services"))
        add_case(cases, "S019", "Dashboard Client Tokens card link", lambda: nav_link(page, ctx, "/", "Client Tokens", "/clients"))
        add_case(cases, "S020", "Dashboard 24h Tool Calls card link", lambda: nav_link(page, ctx, "/", "24h Tool Calls", "/logs"))

        # Services list and service detail (21-45)
        add_case(cases, "S021", "Services page shows all demo services", lambda: load_with_token(page, ctx, "/services", "표시 9개 / 전체 9개"))
        add_case(cases, "S022", "Services search filters demo_ops", lambda: service_search(page, ctx, "demo_ops", "Personal Ops Desk MCP"))
        add_case(cases, "S023", "Services search no match empty state", lambda: service_search_empty(page, ctx))
        add_case(cases, "S024", "Services status filter all", lambda: service_filter(page, ctx, "모든 상태", "표시 9개 / 전체 9개"))
        add_case(cases, "S025", "Services status filter active", lambda: service_filter(page, ctx, "active", "전체 9개"))
        add_case(cases, "S026", "Services sort name", lambda: service_sort(page, ctx, "이름순"))
        add_case(cases, "S027", "Services sort tools", lambda: service_sort(page, ctx, "도구 많은순"))
        add_case(cases, "S028", "Services sort recent", lambda: service_sort(page, ctx, "최근 업데이트순"))
        add_case(cases, "S029", "Service detail link opens", lambda: first_service_detail(page, ctx))
        add_case(cases, "S030", "Service detail back link", lambda: detail_back(page, ctx))
        add_case(cases, "S031", "Service detail Validate button", lambda: detail_validate(page, ctx))
        add_case(cases, "S032", "Service detail Credential shortcut", lambda: detail_credential_shortcut(page, ctx))
        for idx, tab in enumerate(["Overview", "Tools", "Validation", "Credential", "Logs", "Settings"], start=33):
            add_case(cases, f"S{idx:03d}", f"Service detail tab {tab}", lambda tab=tab: detail_tab(page, ctx, tab))
        add_case(cases, "S039", "Bulk Validate click keeps list visible", lambda: bulk_validate_keeps_services(page, ctx))
        add_case(cases, "S040", "Bulk Validate keeps playground tools visible", lambda: assert_api_count(ctx, "/v1/playground/tools/list?limit=100", 1))
        add_case(cases, "S041", "Create throwaway service via form", lambda: create_temp_service(page, ctx))
        add_case(cases, "S042", "Validate throwaway service", lambda: validate_temp_service(page, ctx))
        add_case(cases, "S043", "Add throwaway service to toolbox", lambda: add_temp_to_toolbox(page, ctx))
        add_case(cases, "S044", "Throwaway service detail settings tab", lambda: temp_detail_settings(page, ctx))
        add_case(cases, "S045", "Throwaway service delete guard disabled until confirm", lambda: temp_delete_guard(page, ctx))

        # Toolbox (46-60)
        add_case(cases, "S046", "Toolbox page loads", lambda: load_with_token(page, ctx, "/toolbox", "기본 도구함"))
        add_case(cases, "S047", "Toolbox has cached tools", lambda: visible_text(page, "cached tools"))
        add_case(cases, "S048", "Toolbox first service disable button works", lambda: toggle_first_toolbox_item(page, ctx, "Disable service"))
        add_case(cases, "S049", "Toolbox first service enable button works", lambda: toggle_first_toolbox_item(page, ctx, "Enable service"))
        add_case(cases, "S050", "Toolbox temp item appears", lambda: temp_toolbox_visible(page, ctx))
        add_case(cases, "S051", "Toolbox temp disable", lambda: toggle_temp_toolbox(page, ctx, "Disable service"))
        add_case(cases, "S052", "Toolbox temp enable", lambda: toggle_temp_toolbox(page, ctx, "Enable service"))
        add_case(cases, "S053", "Toolbox temp remove button", lambda: remove_temp_toolbox(page, ctx))
        add_case(cases, "S054", "Toolbox page still has real services", lambda: visible_text(page, "demo_ops"))
        add_case(cases, "S055", "Toolbox service status badges visible", lambda: visible_text(page, "service enabled"))
        add_case(cases, "S056", "Toolbox callable counts visible", lambda: visible_text(page, "callable"))
        add_case(cases, "S057", "Toolbox visible_only counts visible", lambda: visible_text(page, "visible_only"))
        add_case(cases, "S058", "Toolbox hidden counts visible", lambda: visible_text(page, "hidden"))
        add_case(cases, "S059", "Toolbox disabled tools counts visible", lambda: visible_text(page, "disabled tools"))
        add_case(cases, "S060", "Toolbox empty state not shown with real items", lambda: assert_not_text(page, "도구함이 비어 있습니다."))

        # Playground (61-80)
        add_case(cases, "S061", "Playground auto-loads tools", lambda: playground_loaded(page, ctx))
        add_case(cases, "S062", "Playground load button reloads tools", lambda: playground_load_button(page, ctx))
        add_case(cases, "S063", "Tool select changes selection", lambda: playground_select_tool(page, ctx, "demo_ops.ops_status"))
        add_case(cases, "S064", "Pin button pins tool", lambda: playground_pin(page, ctx))
        add_case(cases, "S065", "Pin button unpins tool", lambda: playground_unpin(page, ctx))
        add_case(cases, "S066", "JSON mode button works when schema exists", lambda: playground_json_mode(page, ctx))
        add_case(cases, "S067", "Schema form toggle works", lambda: playground_schema_mode(page, ctx))
        add_case(cases, "S068", "Manual JSON edit accepts valid JSON", lambda: playground_set_json(page, ctx, "{}"))
        add_case(cases, "S069", "Call tool returns JSON result", lambda: playground_call(page, ctx))
        add_case(cases, "S070", "Replay button calls again", lambda: playground_replay(page, ctx))
        add_case(cases, "S071", "Invalid JSON shows parse error", lambda: playground_invalid_json(page, ctx))
        add_case(cases, "S072", "Read-only search tool call", lambda: api_tool_call(ctx, "demo_knowledge.note_search", {"query": "ops"}))
        add_case(cases, "S073", "Task list tool call", lambda: api_tool_call(ctx, "demo_tasks.task_list", {}))
        add_case(cases, "S074", "Bookmark search tool call", lambda: api_tool_call(ctx, "demo_bookmarks.bookmark_search", {"query": "demo"}))
        add_case(cases, "S075", "Finance summary tool call", lambda: api_tool_call(ctx, "demo_finance.ledger_summary", {}))
        add_case(cases, "S076", "Home lab device list tool call", lambda: api_tool_call(ctx, "demo_home_lab.device_list", {}))
        add_case(cases, "S077", "Travel itinerary list tool call", lambda: api_tool_call(ctx, "demo_travel.itinerary_list", {}))
        add_case(cases, "S078", "Design asset search tool call", lambda: api_tool_call(ctx, "demo_design.asset_search", {"query": "demo"}))
        add_case(cases, "S079", "Unknown tool fails safely", lambda: api_unknown_tool(ctx))
        add_case(cases, "S080", "Playground does not store raw token text", lambda: assert_not_text(page, ctx.token))

        # Clients / settings / logs / responsive / cleanup (81-100)
        add_case(cases, "S081", "Clients page explains AI client", lambda: load_with_token(page, ctx, "/clients", "외부 AI 도구가 CoreMCP 도구함을 호출할 수 있게"))
        add_case(cases, "S082", "Client type select Codex", lambda: client_type_select(page, ctx, "codex_cli"))
        add_case(cases, "S083", "Client type select Claude", lambda: client_type_select(page, ctx, "claude_code"))
        add_case(cases, "S084", "Client copy config button", lambda: client_copy_config(page, ctx))
        add_case(cases, "S085", "Create throwaway external connection+token", lambda: client_create_temp(page, ctx))
        add_case(cases, "S086", "Revoke throwaway connection", lambda: client_revoke_temp(page, ctx))
        add_case(cases, "S087", "One-time token button returns prompt", lambda: client_one_time(page, ctx))
        add_case(cases, "S088", "Settings page loads tokens", lambda: load_with_token(page, ctx, "/settings", "Token 관리"))
        add_case(cases, "S089", "Settings revoke buttons visible if tokens exist", lambda: settings_revoke_visible_or_skip(ctx, page))
        add_case(cases, "S090", "Logs page loads", lambda: load_with_token(page, ctx, "/logs", "최근 tool invocation / audit"))
        add_case(cases, "S091", "Logs status filter all", lambda: logs_filter(page, ctx, "all"))
        add_case(cases, "S092", "Logs status filter success", lambda: logs_filter(page, ctx, "success"))
        add_case(cases, "S093", "Logs status filter error", lambda: logs_filter(page, ctx, "error"))
        add_case(cases, "S094", "Mobile dashboard nav renders", lambda: mobile_route(page, ctx, "/", "Dashboard"))
        add_case(cases, "S095", "Mobile services renders", lambda: mobile_route(page, ctx, "/services", "MCP 추가/등록"))
        add_case(cases, "S096", "Mobile playground renders tools", lambda: mobile_route(page, ctx, "/playground", "개 도구 로드됨"))
        add_case(cases, "S097", "No browser console errors", lambda: assert_no_console_errors(console_errors))
        add_case(cases, "S098", "No non-RSC request failures", lambda: assert_no_request_failures(request_failures))
        add_case(cases, "S099", "Cleanup throwaway service", lambda: cleanup_temp_service(ctx))
        add_case(cases, "S100", "Final tool count remains non-empty", lambda: assert_api_count(ctx, "/v1/playground/tools/list?limit=100", 40))

        assert len(cases) == 100, len(cases)
        run_cases(ctx, cases)
        page.screenshot(path=str(config.screenshots_dir / "final_state.png"), full_page=True)
        browser.close()

    results_path = config.out_dir / "button-scenarios-results.json"
    results_path.write_text(json.dumps(ctx.results, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = [r for r in ctx.results if r["status"] == "FAIL"]
    skips = [r for r in ctx.results if r["status"] == "SKIP"]
    print(f"RESULTS: {results_path}")
    print(f"SUMMARY: total={len(ctx.results)} pass={len(ctx.results)-len(failures)-len(skips)} fail={len(failures)} skip={len(skips)}")
    return 1 if failures else 0


# Helpers implemented below keep scenario definitions readable.
def assert_api(ctx: ScenarioContext, path: str, key: str, expected: Any, *, auth: bool = True) -> None:
    code, payload = api(ctx, "GET", path, auth=auth)
    assert code == 200, (code, payload)
    assert payload.get(key) == expected, payload


def assert_api_count(ctx: ScenarioContext, path: str, minimum: int) -> None:
    code, payload = api(ctx, "GET", path)
    assert code == 200, (code, payload)
    assert len(payload.get("items", [])) >= minimum, payload


def load_with_token(page: Page, ctx: ScenarioContext, path: str, text: str) -> None:
    goto(page, ctx, path)
    set_token(page, ctx.token)
    page.reload(wait_until="networkidle", timeout=UI_TIMEOUT_MS)
    visible_text(page, text)


def nav_link(page: Page, ctx: ScenarioContext, start: str, name: str, suffix: str) -> None:
    load_with_token(page, ctx, start, "CoreMCP")
    target = suffix if suffix else "/"
    link = page.locator(f'a[href="{target}"]').first
    expect(link).to_be_visible(timeout=UI_TIMEOUT_MS)
    link.click(timeout=UI_TIMEOUT_MS)
    page.wait_for_url(f"**{target}", timeout=UI_TIMEOUT_MS)
    page.wait_for_load_state("networkidle", timeout=UI_TIMEOUT_MS)
    expect_path(page, suffix)


def health_button(page: Page, ctx: ScenarioContext) -> None:
    load_with_token(page, ctx, "/", "Health")
    click_button(page, "Health")
    visible_text(page, "API 상태: ok")


def refresh_button(page: Page, ctx: ScenarioContext) -> None:
    load_with_token(page, ctx, "/", "새로고침")
    click_button(page, "새로고침")
    page.wait_for_timeout(800)
    visible_text(page, "Dashboard")


def token_clear_restore(page: Page, ctx: ScenarioContext) -> None:
    load_with_token(page, ctx, "/", "삭제")
    click_button(page, "삭제")
    visible_text(page, "sessionStorage에 저장된 admin token을 삭제했습니다.")
    set_token(page, ctx.token)
    page.reload(wait_until="networkidle", timeout=UI_TIMEOUT_MS)
    visible_text(page, "Dashboard")


def service_search(page: Page, ctx: ScenarioContext, query: str, expected: str) -> None:
    load_with_token(page, ctx, "/services", "MCP 추가/등록")
    page.get_by_placeholder("서비스명, slug, URL, category 검색").fill(query)
    visible_text(page, expected)


def service_search_empty(page: Page, ctx: ScenarioContext) -> None:
    load_with_token(page, ctx, "/services", "MCP 추가/등록")
    page.get_by_placeholder("서비스명, slug, URL, category 검색").fill("no_such_service_qa")
    visible_text(page, "검색 조건에 맞는 MCP가 없습니다.")


def service_filter(page: Page, ctx: ScenarioContext, option: str, expected: str) -> None:
    load_with_token(page, ctx, "/services", "MCP 추가/등록")
    page.locator("article#services select").nth(0).select_option(label=option)
    visible_text(page, expected)


def service_sort(page: Page, ctx: ScenarioContext, option: str) -> None:
    load_with_token(page, ctx, "/services", "MCP 추가/등록")
    page.locator("article#services select").nth(1).select_option(label=option)
    visible_text(page, "표시")


def first_service_detail(page: Page, ctx: ScenarioContext) -> None:
    load_with_token(page, ctx, "/services", "MCP 추가/등록")
    click_link(page, "Detail")
    visible_text(page, "Overview")


def detail_back(page: Page, ctx: ScenarioContext) -> None:
    first_service_detail(page, ctx)
    page.get_by_role("link", name="← Services").click(timeout=UI_TIMEOUT_MS)
    page.wait_for_url("**/services", timeout=UI_TIMEOUT_MS)
    expect_path(page, "/services")


def detail_validate(page: Page, ctx: ScenarioContext) -> None:
    first_service_detail(page, ctx)
    click_button(page, "Validate")
    page.wait_for_timeout(600)
    visible_text(page, "Validation")


def detail_credential_shortcut(page: Page, ctx: ScenarioContext) -> None:
    first_service_detail(page, ctx)
    click_button(page, "Credential")
    visible_text(page, "Credential 등록/회전")


def detail_tab(page: Page, ctx: ScenarioContext, tab: str) -> None:
    first_service_detail(page, ctx)
    page.get_by_role("tab", name=tab).click(timeout=UI_TIMEOUT_MS)
    visible_text(page, tab)


def bulk_validate_keeps_services(page: Page, ctx: ScenarioContext) -> None:
    load_with_token(page, ctx, "/services", "표시 9개 / 전체 9개")
    page.evaluate("""Array.from(document.querySelectorAll('button')).filter(b => b.textContent?.trim() === 'Validate').forEach(b => b.click())""")
    page.wait_for_timeout(800)
    visible_text(page, "전체 9개")


def create_temp_service(page: Page, ctx: ScenarioContext) -> None:
    cleanup(ctx)
    load_with_token(page, ctx, "/services", "MCP 추가/등록")
    page.get_by_placeholder("Service name").fill("QA Button Temp MCP")
    page.get_by_role("textbox", name="slug", exact=True).fill(ctx.temp_service_slug)
    page.get_by_placeholder("https://.../mcp").fill("http://127.0.0.1:8791/personal-ops/mcp")
    click_button(page, "등록")
    page.wait_for_timeout(800)
    find_temp_service(ctx)
    assert ctx.temp_service_id, "temp service id missing after create"


def validate_temp_service(page: Page, ctx: ScenarioContext) -> None:
    find_temp_service(ctx)
    assert ctx.temp_service_id, "temp service missing"
    code, payload = api(ctx, "POST", f"/v1/mcp-services/{ctx.temp_service_id}/validate")
    assert code == 200, (code, payload)
    code, payload = api(ctx, "GET", f"/v1/mcp-services/{ctx.temp_service_id}")
    assert code == 200 and payload.get("status") in {"active", "validating"}, payload


def add_temp_to_toolbox(page: Page, ctx: ScenarioContext) -> None:
    find_temp_service(ctx)
    assert ctx.temp_service_id, "temp service missing"
    code, boxes = api(ctx, "GET", "/v1/toolboxes?limit=20")
    assert code == 200, boxes
    toolbox_id = next((item["id"] for item in boxes.get("items", []) if item.get("is_default")), "tbx_default")
    code, payload = api(ctx, "POST", f"/v1/toolboxes/{toolbox_id}/items", {"service_id": ctx.temp_service_id, "enabled": True})
    assert code in {200, 201, 409}, (code, payload)
    goto(page, ctx, "/toolbox")
    visible_text(page, ctx.temp_service_slug)


def temp_detail_settings(page: Page, ctx: ScenarioContext) -> None:
    find_temp_service(ctx)
    assert ctx.temp_service_id, "temp service missing"
    load_with_token(page, ctx, f"/services/{ctx.temp_service_id}", "Settings")
    page.get_by_role("tab", name="Settings").click(timeout=UI_TIMEOUT_MS)
    visible_text(page, "Service 설정과 삭제")


def temp_delete_guard(page: Page, ctx: ScenarioContext) -> None:
    find_temp_service(ctx)
    assert ctx.temp_service_id, "temp service missing"
    load_with_token(page, ctx, f"/services/{ctx.temp_service_id}", "Settings")
    page.get_by_role("tab", name="Settings").click(timeout=UI_TIMEOUT_MS)
    expect(page.get_by_role("button", name="Service 삭제")).to_be_disabled(timeout=UI_TIMEOUT_MS)


def toggle_first_toolbox_item(page: Page, ctx: ScenarioContext, button_name: str) -> None:
    load_with_token(page, ctx, "/toolbox", "기본 도구함")
    click_button(page, button_name)
    page.wait_for_timeout(700)
    visible_text(page, "기본 도구함")


def temp_toolbox_visible(page: Page, ctx: ScenarioContext) -> None:
    add_temp_to_toolbox(page, ctx)
    visible_text(page, ctx.temp_service_slug)


def toggle_temp_toolbox(page: Page, ctx: ScenarioContext, button_name: str) -> None:
    add_temp_to_toolbox(page, ctx)
    row = page.locator(".cm-panel-subtle", has_text=ctx.temp_service_slug).first
    current_text = row.inner_text(timeout=UI_TIMEOUT_MS)
    if button_name not in current_text:
        alt = "Disable service" if button_name == "Enable service" else "Enable service"
        if alt in current_text:
            row.get_by_role("button", name=alt).click(timeout=UI_TIMEOUT_MS)
            page.wait_for_timeout(700)
            row = page.locator(".cm-panel-subtle", has_text=ctx.temp_service_slug).first
    row.get_by_role("button", name=button_name).click(timeout=UI_TIMEOUT_MS)
    page.wait_for_timeout(700)
    visible_text(page, ctx.temp_service_slug)


def remove_temp_toolbox(page: Page, ctx: ScenarioContext) -> None:
    add_temp_to_toolbox(page, ctx)
    page.once("dialog", lambda dialog: dialog.accept())
    row = page.locator(".cm-panel-subtle", has_text=ctx.temp_service_slug).first
    row.get_by_role("button", name="Remove").click(timeout=UI_TIMEOUT_MS)
    page.wait_for_timeout(700)
    assert ctx.temp_service_slug not in page.locator("body").inner_text(timeout=UI_TIMEOUT_MS)


def playground_loaded(page: Page, ctx: ScenarioContext) -> None:
    load_with_token(page, ctx, "/playground", "개 도구 로드됨")
    text = page.locator("body").inner_text(timeout=UI_TIMEOUT_MS)
    assert "40개 도구 로드됨" in text or "41개 도구 로드됨" in text or "45개 도구 로드됨" in text, text[:500]


def playground_load_button(page: Page, ctx: ScenarioContext) -> None:
    playground_loaded(page, ctx)
    click_button(page, "도구 목록 불러오기")
    visible_text(page, "개 도구 로드됨")


def playground_select_tool(page: Page, ctx: ScenarioContext, tool: str) -> None:
    playground_loaded(page, ctx)
    page.locator("#tool-select").select_option(tool)
    assert page.locator("#tool-select").input_value() == tool


def playground_pin(page: Page, ctx: ScenarioContext) -> None:
    playground_select_tool(page, ctx, "demo_ops.ops_status")
    click_button(page, "☆ Pin")
    visible_text(page, "★ Pinned")


def playground_unpin(page: Page, ctx: ScenarioContext) -> None:
    playground_pin(page, ctx)
    click_button(page, "★ Pinned")
    visible_text(page, "☆ Pin")


def playground_json_mode(page: Page, ctx: ScenarioContext) -> None:
    playground_select_tool(page, ctx, "demo_knowledge.note_search")
    click_button(page, "JSON 직접 편집")
    visible_text(page, "Arguments JSON")


def playground_schema_mode(page: Page, ctx: ScenarioContext) -> None:
    playground_json_mode(page, ctx)
    click_button(page, "Schema form으로 전환")
    visible_text(page, "Schema form")


def playground_set_json(page: Page, ctx: ScenarioContext, value: str) -> None:
    playground_json_mode(page, ctx)
    page.locator("textarea").fill(value)
    assert page.locator("textarea").input_value() == value


def playground_call(page: Page, ctx: ScenarioContext) -> None:
    playground_select_tool(page, ctx, "demo_ops.ops_status")
    click_button(page, "Call tool")
    visible_text(page, '"structuredContent"')


def playground_replay(page: Page, ctx: ScenarioContext) -> None:
    playground_call(page, ctx)
    click_button(page, "Replay")
    visible_text(page, "결과 diff")


def playground_invalid_json(page: Page, ctx: ScenarioContext) -> None:
    playground_select_tool(page, ctx, "demo_knowledge.note_search")
    click_button(page, "JSON 직접 편집")
    page.locator("textarea").fill("{")
    expect(page.get_by_role("button", name="Call tool")).to_be_disabled(timeout=UI_TIMEOUT_MS)
    visible_text(page, "JSON 오류")


def api_tool_call(ctx: ScenarioContext, name: str, args: dict[str, Any]) -> None:
    code, payload = api(ctx, "POST", "/v1/playground/tools/call", {"exposed_name": name, "arguments": args})
    assert code == 200, (code, payload)
    result = payload.get("result")
    assert isinstance(result, dict) and result.get("isError") is False, payload


def api_unknown_tool(ctx: ScenarioContext) -> None:
    code, payload = api(ctx, "POST", "/v1/playground/tools/call", {"exposed_name": "qa.no_such_tool", "arguments": {}})
    assert code in {400, 404, 422} or (code == 200 and "error" in payload), (code, payload)


def assert_not_text(page: Page, text: str) -> None:
    visible = page.locator("body").inner_text(timeout=UI_TIMEOUT_MS)
    # The sidebar intentionally shows a masked admin-token preview; raw full
    # token must never be rendered.
    assert text not in visible, "sensitive raw text unexpectedly rendered"


def client_type_select(page: Page, ctx: ScenarioContext, value: str) -> None:
    load_with_token(page, ctx, "/clients", "연결된 AI client")
    page.locator("article#clients select").first.select_option(value)
    assert page.locator("article#clients select").first.input_value() == value


def client_copy_config(page: Page, ctx: ScenarioContext) -> None:
    client_type_select(page, ctx, "codex_cli")
    click_button(page, "Copy config")
    page.wait_for_timeout(500)
    body = page.locator("body").inner_text(timeout=UI_TIMEOUT_MS)
    assert "Copied" in body or "복사하지 못했습니다" in body


def client_create_temp(page: Page, ctx: ScenarioContext) -> None:
    load_with_token(page, ctx, "/clients", "연결된 AI client")
    ctx.temp_client_name = f"QA Client {int(time.time())}"
    page.locator("article#clients input").first.fill(ctx.temp_client_name)
    click_button(page, "등록+Token 발급")
    page.wait_for_timeout(1000)
    # Resolve temp connection through API without logging token value.
    code, payload = api(ctx, "GET", "/v1/external-connections?limit=100")
    assert code == 200, payload
    candidates = [
        item
        for item in payload.get("items", [])
        if item.get("client_name") == ctx.temp_client_name and item.get("status") != "revoked"
    ]
    assert candidates, "temp connection missing"
    ctx.temp_connection_id = candidates[0]["id"]


def client_revoke_temp(page: Page, ctx: ScenarioContext) -> None:
    if not ctx.temp_connection_id:
        client_create_temp(page, ctx)
    load_with_token(page, ctx, "/clients", "연결된 AI client")
    page.once("dialog", lambda dialog: dialog.accept())
    row = page.locator(".cm-panel-subtle", has_text=ctx.temp_client_name or "QA Client").first
    row.get_by_role("button", name="Revoke").click(timeout=UI_TIMEOUT_MS)
    page.wait_for_timeout(700)
    code, payload = api(ctx, "GET", "/v1/external-connections?limit=100")
    assert code == 200, payload
    current = [item for item in payload.get("items", []) if item.get("id") == ctx.temp_connection_id]
    assert not current or current[0].get("status") == "revoked", current


def client_one_time(page: Page, ctx: ScenarioContext) -> None:
    load_with_token(page, ctx, "/clients", "연결된 AI client")
    click_button(page, "One-time Token")
    visible_text(page, "One-time token을 발급했습니다")


def settings_revoke_visible_or_skip(ctx: ScenarioContext, page: Page) -> None:
    load_with_token(page, ctx, "/settings", "Token 관리")
    # Do not revoke real tokens here; verifying visibility is enough.
    body = page.locator("body").inner_text(timeout=UI_TIMEOUT_MS)
    assert "Revoke" in body or "active token이 없습니다" in body


def logs_filter(page: Page, ctx: ScenarioContext, value: str) -> None:
    normalized = "errors" if value == "error" else value
    load_with_token(page, ctx, "/logs", "최근 tool invocation / audit")
    page.locator("section#logs select").first.select_option(normalized)
    visible_text(page, "최근 tool invocation / audit")


def mobile_route(page: Page, ctx: ScenarioContext, path: str, text: str) -> None:
    page.set_viewport_size({"width": 390, "height": 900})
    goto(page, ctx, path)
    set_token(page, ctx.token)
    page.reload(wait_until="networkidle", timeout=UI_TIMEOUT_MS)
    if text in {"Dashboard", "MCP 추가/등록", "Playground"}:
        expect(page.locator("h1", has_text=text).first).to_be_visible(timeout=UI_TIMEOUT_MS)
    else:
        visible_text(page, text)
    page.set_viewport_size({"width": 1440, "height": 1050})


def assert_no_console_errors(errors: list[str]) -> None:
    critical = [e for e in errors if "ResizeObserver" not in e]
    assert not critical, critical[-5:]


def assert_no_request_failures(failures: list[dict[str, str]]) -> None:
    # Navigation/reload can abort in-flight RSC/API/image requests during this
    # stress suite. Treat only non-abort network failures as defects.
    critical = [
        failure
        for failure in failures
        if "ERR_ABORTED" not in failure.get("error", "")
        and "NS_BINDING_ABORTED" not in failure.get("error", "")
        and "Operation canceled" not in failure.get("error", "")
    ]
    assert not critical, critical[-5:]


def cleanup_temp_service(ctx: ScenarioContext) -> None:
    cleanup(ctx)
    find_temp_service(ctx)
    assert ctx.temp_service_id is None, "temp service still present after cleanup"


if __name__ == "__main__":
    raise SystemExit(main())
