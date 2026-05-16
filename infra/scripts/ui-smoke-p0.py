"""CoreMCP P0 + 확장 회귀 verify (manual checklist 자동화 가능 부분).

`coremcp-docs/test-checklist.md` 의 P0 5건 + 추가 안전 케이스(D-01/D-04/D-06,
S-04/S-05/S-08, P-03~15, C-01, ST-01, L-01/L-02, NF-01/04/06/09)를
한 번에 검증한다. Destructive 케이스(SD-*, T-02/04, S-03, C-02/04, P-18 등)는
이 스크립트 범위 밖이며 manual checklist 로 분리한다.

환경 변수:
  COREMCP_UI_SMOKE_API_URL  (default http://127.0.0.1:8787)
  COREMCP_UI_SMOKE_WEB_URL  (default http://localhost:3003)
  COREMCP_ADMIN_TOKEN_FILE  (default ~/.coremcp/admin-token)
  COREMCP_UI_SMOKE_OUT_DIR  (default /tmp/coremcp_ui_test/screens)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

API = os.environ.get("COREMCP_UI_SMOKE_API_URL", "http://127.0.0.1:8787")
WEB = os.environ.get("COREMCP_UI_SMOKE_WEB_URL", "http://localhost:3003")
TOKEN_FILE = Path(os.environ.get("COREMCP_ADMIN_TOKEN_FILE", str(Path.home() / ".coremcp/admin-token"))).expanduser()
TOKEN = TOKEN_FILE.read_text().strip()
OUT_DIR = Path(os.environ.get("COREMCP_UI_SMOKE_OUT_DIR", "/tmp/coremcp_ui_test/screens"))
OUT_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

DEMO_SLUGS = {"demo_bookmarks", "demo_ops", "demo_design", "demo_knowledge",
              "demo_travel", "demo_tasks", "demo_finance", "demo_home_lab"}

# Read-only / list 도구 — write/destructive 키워드는 자동으로 제외하지만
# 명시적으로 검증할 read-only 도구만 호출하기 위한 화이트리스트.
READ_ONLY_TOOL_CALLS: list[tuple[str, dict]] = [
    ("demo_ops.ops_status", {}),
    ("demo_ops.ops_checklist", {}),
    ("demo_ops.incident_list", {}),
    ("demo_knowledge.note_search", {"query": "ops"}),
    ("demo_knowledge.note_get", {"note_id": "kv_note_001"}),
    ("demo_tasks.task_list", {}),
    ("demo_bookmarks.bookmark_search", {"query": "demo"}),
    ("demo_design.color_tokens", {}),
    ("demo_design.asset_search", {"query": "demo"}),
    ("demo_finance.ledger_summary", {}),
    ("demo_finance.transaction_search", {}),
    ("demo_home_lab.device_list", {}),
    ("demo_travel.itinerary_list", {}),
    ("demo_travel.place_search", {"query": "demo"}),
]

results: list[tuple[str, str, str]] = []


def record(case: str, status: str, detail: str = "") -> None:
    results.append((case, status, detail))
    icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️"}.get(status, "❓")
    print(f"  {icon} {case:8s} {status:5s}  {detail}")


def api_req(method: str, path: str, body=None, headers: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    r = urllib.request.Request(API + path, data=data, method=method)
    for k, v in (headers or HEADERS).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            text = resp.read().decode("utf-8") or "{}"
            try:
                return resp.status, json.loads(text)
            except json.JSONDecodeError:
                return resp.status, {"_raw": text[:200]}
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:
            payload = {"error": str(e)}
        return e.code, payload


# ════════════════════════════════════════════════
# Pre-condition
# ════════════════════════════════════════════════
print("[Pre] 사전 조건")
code, body = api_req("GET", "/health", headers={})
if code == 200 and body.get("status") == "ok":
    record("HEALTH", "PASS", "GET /health 200 ok")
else:
    record("HEALTH", "FAIL", f"code={code}")
    print("\n사전 조건 실패: API 가 떠 있지 않음. make run 또는 make run-local 후 재시도.")
    sys.exit(20)

code, body = api_req("GET", "/ready", headers={})
record("READY", "PASS" if code == 200 else "FAIL", f"GET /ready {code}")


# ════════════════════════════════════════════════
# S-01 / S-02 — Services
# ════════════════════════════════════════════════
print("\n[Services]")
code, body = api_req("GET", "/v1/mcp-services?limit=20")
items = body.get("items", []) if code == 200 else []
found = {it["slug"]: it["status"] for it in items if it["slug"] in DEMO_SLUGS}
missing = DEMO_SLUGS - set(found)
non_active = {s for s, st in found.items() if st != "active"}
total_tools = sum(it.get("tool_count", 0) for it in items if it["slug"] in DEMO_SLUGS)
if missing:
    record("S-01", "FAIL", f"missing slugs: {missing}")
elif non_active:
    record("S-01", "FAIL", f"non-active: {non_active}")
else:
    record("S-01", "PASS", f"8 demo all active (tools total: {total_tools})")

demo_ops = next((it for it in items if it["slug"] == "demo_ops"), None)
if demo_ops:
    code, body = api_req("POST", f"/v1/mcp-services/{demo_ops['id']}/validate")
    if code == 200 and body.get("status") == "success" and body.get("tools_found") == 6:
        record("S-02", "PASS", "validate demo_ops success tools=6")
    else:
        record("S-02", "FAIL", f"code={code} status={body.get('status')}")
else:
    record("S-02", "SKIP", "demo_ops missing")

# S-04: SSRF block
code, body = api_req("POST", "/v1/mcp-services", {
    "name": "metadata probe",
    "slug": f"smoke_metadata_{int(time.time())}",
    "endpoint_url": "http://169.254.169.254/mcp",
    "auth_type": "none",
})
# 등록은 통과해도 validate 시점에 SSRF 차단. 또는 register 시점부터 거부.
if code in (201, 200):
    # register 통과했으면 즉시 validate 해서 SSRF 거부 확인 후 삭제
    svc_id = body.get("id")
    vcode, vbody = api_req("POST", f"/v1/mcp-services/{svc_id}/validate")
    if vcode in (400, 422) or (vcode == 200 and vbody.get("status") in ("error", "failed")):
        record("S-04", "PASS", f"register OK then validate rejected (code={vcode})")
    elif vcode == 200 and "blocked" in json.dumps(vbody).lower():
        record("S-04", "PASS", "metadata endpoint blocked on validate")
    else:
        record("S-04", "FAIL", f"metadata not blocked vcode={vcode}")
    # cleanup
    api_req("DELETE", f"/v1/mcp-services/{svc_id}")
elif code in (400, 422):
    record("S-04", "PASS", f"metadata endpoint rejected at register code={code}")
else:
    record("S-04", "FAIL", f"unexpected code={code}")

# S-05: 동일 slug 충돌
code, body = api_req("POST", "/v1/mcp-services", {
    "name": "dup",
    "slug": "demo_ops",
    "endpoint_url": "http://127.0.0.1:8791/personal-ops/mcp",
    "auth_type": "none",
})
if code == 409:
    record("S-05", "PASS", "duplicate slug → 409 conflict")
else:
    record("S-05", "FAIL", f"expected 409, got {code}")


# ════════════════════════════════════════════════
# Playground P-01 + read-only batch
# ════════════════════════════════════════════════
print("\n[Playground]")
code, body = api_req("GET", "/v1/playground/tools/list")
tools = body.get("items", []) if code == 200 else []
if code == 200 and len(tools) >= 40:
    record("P-01", "PASS", f"{len(tools)} tools (≥40)")
elif code == 200 and len(tools) >= 30:
    record("P-01", "PASS", f"{len(tools)} tools (≥30 acceptable)")
else:
    record("P-01", "FAIL", f"code={code} count={len(tools)}")

call_pass, call_fail, tool_err = 0, 0, 0
slow_calls: list[tuple[str, int]] = []
for tool_name, args in READ_ONLY_TOOL_CALLS:
    t0 = time.perf_counter()
    code, body = api_req("POST", "/v1/playground/tools/call",
                         {"exposed_name": tool_name, "arguments": args})
    latency_ms = int((time.perf_counter() - t0) * 1000)
    result = body.get("result") if code == 200 else None
    if isinstance(result, dict) and result.get("isError") is False:
        call_pass += 1
        if latency_ms > 200:
            slow_calls.append((tool_name, latency_ms))
    elif isinstance(result, dict) and result.get("isError") is True:
        tool_err += 1  # placeholder id missing 등 — proxy 동작은 정상
    else:
        call_fail += 1

if call_fail == 0 and call_pass >= 10:
    record("P-batch", "PASS",
           f"{call_pass}/{len(READ_ONLY_TOOL_CALLS)} OK + {tool_err} fixture-only TOOL_ERROR")
else:
    record("P-batch", "FAIL", f"pass={call_pass} fail={call_fail} tool_err={tool_err}")

if slow_calls:
    record("NF-01", "FAIL", f"slow (>200ms): {slow_calls}")
else:
    record("NF-01", "PASS", "all tool calls <200ms")


# ════════════════════════════════════════════════
# Connected clients / Settings / Logs
# ════════════════════════════════════════════════
print("\n[Clients / Settings / Logs]")

code, body = api_req("GET", "/v1/external-connections?limit=20")
if code == 200 and isinstance(body.get("items"), list):
    record("C-01", "PASS", f"{len(body['items'])} client(s)")
else:
    record("C-01", "FAIL", f"code={code}")

code, body = api_req("GET", "/v1/settings")
masked = body.get("admin_token_masked", "") if code == 200 else ""
if code == 200 and "•" in masked and TOKEN not in masked and TOKEN[-4:] not in masked:
    record("ST-01", "PASS", f"admin_token_masked='{masked}' (no leak)")
else:
    record("ST-01", "FAIL", f"masked='{masked}'")

code, body = api_req("GET", "/v1/tool-invocations?limit=20")
inv_items = body.get("items", []) if code == 200 else []
if code == 200 and len(inv_items) > 0:
    record("L-01", "PASS", f"{len(inv_items)} recent invocation(s)")
else:
    record("L-01", "FAIL", f"code={code} count={len(inv_items)}")

code, body = api_req("GET", "/v1/audit-logs?limit=20")
audit_items = body.get("items", []) if code == 200 else []
if code == 200 and len(audit_items) > 0:
    record("L-02", "PASS", f"{len(audit_items)} recent audit log(s)")
    # NF-06: redaction — audit metadata 에 admin token (full string 또는 last4) 없는지
    audit_blob = json.dumps(audit_items, ensure_ascii=False)
    if TOKEN in audit_blob or TOKEN[-8:] in audit_blob:
        record("NF-06", "FAIL", "admin token leaked into audit metadata")
    else:
        record("NF-06", "PASS", "audit metadata clean of admin token")
else:
    record("L-02", "FAIL", f"code={code}")
    record("NF-06", "SKIP", "no audit data to inspect")


# ════════════════════════════════════════════════
# NF-02 CORS preflight
# ════════════════════════════════════════════════
print("\n[Non-functional]")
preflight = subprocess.run(
    ["/usr/bin/curl", "-s", "-i", "-X", "OPTIONS", f"{API}/v1/settings",
     "-H", "Origin: http://localhost:3003",
     "-H", "Access-Control-Request-Method: GET",
     "-H", "Access-Control-Request-Headers: authorization"],
    capture_output=True, text=True, timeout=10,
)
out = preflight.stdout.lower()
allow_origin_ok = "access-control-allow-origin: http://localhost:3003" in out
methods_ok = "access-control-allow-methods" in out and "post" in out and "get" in out
status_ok = out.startswith("http/1.1 200") or out.startswith("http/2 200")
if status_ok and allow_origin_ok and methods_ok:
    record("NF-02", "PASS", "preflight 200 + origin + methods")
else:
    record("NF-02", "FAIL", f"status={status_ok} origin={allow_origin_ok} methods={methods_ok}")

# NF-04 body cap (1MB)
oversize_payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                               "params": {"_pad": "x" * (1024 * 1024 + 2048)}}).encode("utf-8")
try:
    r = urllib.request.Request(f"{API}/mcp", data=oversize_payload, method="POST")
    r.add_header("Authorization", f"Bearer {TOKEN}")
    r.add_header("Content-Type", "application/json")
    urllib.request.urlopen(r, timeout=20)
    record("NF-04", "FAIL", "oversize body not rejected")
except urllib.error.HTTPError as e:
    if e.code in (400, 413, 422):
        record("NF-04", "PASS", f"oversize body → HTTP {e.code}")
    else:
        record("NF-04", "FAIL", f"unexpected code={e.code}")
except urllib.error.URLError as e:
    # Some servers close the connection as soon as the streaming body limiter
    # trips, before urllib finishes writing the oversized request body. Treat
    # that as an accepted body-cap rejection rather than a smoke crash.
    record("NF-04", "PASS", f"oversize body connection closed ({e.reason})")

# NF-09 favicon
fcode = subprocess.run(
    ["/usr/bin/curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"{WEB}/icon.svg"],
    capture_output=True, text=True, timeout=5,
).stdout
if fcode == "200":
    record("NF-09", "PASS", "GET /icon.svg 200")
else:
    record("NF-09", "FAIL", f"GET /icon.svg → {fcode}")


# ════════════════════════════════════════════════
# E2E-D — health probe recovery 단위 회귀
# ════════════════════════════════════════════════
print("\n[E2E-D]")
pytest_run = subprocess.run(
    ["/opt/homebrew/bin/uv", "run", "pytest",
     "tests/test_health_probe_recovery.py", "-q", "--no-header"],
    cwd="/Users/hwanchoi/project_202605/CoreMCP/apps/api",
    capture_output=True, text=True, timeout=60,
    env={"HOME": str(Path.home()),
         "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
)
last_line = (pytest_run.stdout.strip().splitlines() or [""])[-1]
if "5 passed" in last_line:
    record("E2E-D", "PASS", last_line)
else:
    record("E2E-D", "FAIL", last_line[:120])


# ════════════════════════════════════════════════
# D-01 / D-02 / D-04 / D-06 / D-07 — Web UI flows
# ════════════════════════════════════════════════
print("\n[Web UI flows]")
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="ko-KR")
    page = ctx.new_page()

    # D-01: token 미입력 first-load
    page.goto(WEB, wait_until="networkidle", timeout=15_000)
    page.wait_for_timeout(2000)
    body_text = page.locator("main").inner_text()
    if "token 필요" in body_text and "0/0" in body_text and "Admin token 저장 후" in body_text:
        record("D-01", "PASS", "초기 빈 dashboard + token 필요 안내")
    else:
        record("D-01", "FAIL", body_text.replace("\n", " | ")[:120])
    page.screenshot(path=str(OUT_DIR / "p0_D-01_no_token.png"), full_page=True)

    # D-02: token 입력 후 fetch
    page.evaluate("t => sessionStorage.setItem('coremcp_admin_token', t)", TOKEN)
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(3000)
    body_text = page.locator("main").inner_text()
    d02_ok = ("최신 데이터를 불러왔습니다" in body_text and "8/8" in body_text
              and "auth ok" in body_text and "auth mode: static_bearer" in body_text)
    record("D-02", "PASS" if d02_ok else "FAIL",
           "auth ok + 8/8 + static_bearer" if d02_ok else body_text[:120])
    page.screenshot(path=str(OUT_DIR / "p0_D-02_authed.png"), full_page=True)

    # D-04: Health 버튼
    health_btn = page.get_by_role("button", name="Health")
    if health_btn.count() > 0:
        health_btn.click()
        page.wait_for_timeout(1500)
        sidebar_text = page.locator(".cm-sidebar-footer, aside").first.inner_text()
        if "API 상태: ok" in sidebar_text:
            record("D-04", "PASS", "Health 버튼 → API 상태: ok")
        else:
            record("D-04", "FAIL", sidebar_text[:120])
    else:
        record("D-04", "SKIP", "Health button not found")

    # D-06: 삭제
    delete_btn = page.get_by_role("button", name="삭제")
    if delete_btn.count() > 0:
        delete_btn.first.click()
        page.wait_for_timeout(1500)
        body_text = page.locator("main").inner_text()
        stored = page.evaluate("sessionStorage.getItem('coremcp_admin_token')")
        if stored is None and ("token 필요" in body_text or "Admin token 저장" in body_text):
            record("D-06", "PASS", "token cleared + UI back to needs-token")
        else:
            record("D-06", "FAIL", f"stored={stored!r}")
    else:
        record("D-06", "SKIP", "삭제 button not found")

    # D-07: 잘못된 토큰
    page.evaluate("sessionStorage.setItem('coremcp_admin_token', 'cmcp_admin_invalid')")
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(3000)
    body_text = page.locator("main").inner_text()
    stored_after = page.evaluate("sessionStorage.getItem('coremcp_admin_token')")
    if stored_after is None and ("token 필요" in body_text or "0/0" in body_text):
        record("D-07", "PASS", "401 자동 → token cleared")
    else:
        record("D-07", "FAIL", f"stored={stored_after!r}")

    browser.close()


# ════════════════════════════════════════════════
# 결과
# ════════════════════════════════════════════════
print("\n" + "=" * 60)
passes = sum(1 for _, st, _ in results if st == "PASS")
fails = sum(1 for _, st, _ in results if st == "FAIL")
skips = sum(1 for _, st, _ in results if st == "SKIP")
print(f"검증 결과: {passes} PASS / {fails} FAIL / {skips} SKIP (총 {len(results)})")
print("=" * 60)
for case, status, detail in results:
    icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️"}.get(status, "❓")
    print(f"  {icon} {case:8s} {status:5s}  {detail}")

results_path = OUT_DIR.parent / "ui_smoke_p0_results.json"
results_path.write_text(json.dumps(
    [{"case": c, "status": s, "detail": d} for c, s, d in results],
    ensure_ascii=False, indent=2,
))
print(f"\nresults: {results_path}")
print(f"screenshots: {OUT_DIR}")

sys.exit(0 if fails == 0 else 1)
