# CoreMCP MCP Proxy Spec (Personal)

문서 버전: v1.0
작성일: 2026-05-11

---

## 1. 목적
CoreMCP가 하나의 MCP server처럼 보이면서 내부적으로 여러 downstream MCP service tool을 proxy하는 방식을 정의한다.

## 2. Proxy Mode
meta-tool 방식이 아니라 proxy mode 사용. exposed tool 형식: `{service_slug}.{tool_name}` (ADR-019).

금지:
```text
invoke_tool(service, tool, args)
```

권장 (실제):
```text
github.create_issue
notion.search_page
kakao.send_message
```

이유:
- LLM이 자연스럽게 tool 선택
- Claude Code UI에서 namespace 명확

## 3. Tool Name Mapping

### 3.1 Exposed Name 형식
- `{service_slug}.{tool_name}`
- downstream tool name에 `.`이 이미 포함되어 있어도 service slug prefix를 반드시 붙인다. namespace authority는 downstream service가 아니라 CoreMCP registry가 가진다.
- service_slug: lowercase [a-z0-9-]+, 3-32 chars
- tool_name part: lowercase [a-z0-9_]+, 1-48 chars
- 총 길이 **64자 — CoreMCP 정책상의 보수적 제한** (MCP 2025-11-25 spec 자체에는 tool name length의 hard cap 명시 없음, 단 일부 client UI에서 긴 이름이 truncate되거나 regex 검증 실패 가능)
- 본인이 만든 downstream MCP라 이름이 길면 service_tools.original_name은 보존하고 exposed_name만 sanitize

icons는 별도 metadata, name에 영향 없음 (P1-1).
icons object schema: `{src, mimeType, sizes?}` (MCP 2025-11-25 표준, HTML `<img>` 표준 align).

### 3.2 Normalization Rules
입력: downstream tool name
처리:
1. trim
2. Unicode NFKC normalize
3. lowercase 강제 (original_name은 service_tools에 보존)
4. spaces → `_`
5. unsupported chars → `_`
6. multiple `_` collapse → 1개
7. zero-width / RTL override 제거
8. max length 48 chars (slug 포함 64 한계) — CoreMCP 정책. MCP spec hard cap 아님.
9. reserved 거부

Reserved namespace:
```text
core.*
admin.*
internal.*
mcp.*
_meta.*
```

2025-11-25 tool name guidance:
- 영문 [a-z0-9_]+ + dot 1개를 권장 (`{service_slug}.{tool_name}`)
- 64 char cap — CoreMCP 정책상의 보수적 제한 (MCP spec hard cap 아님, 일부 client UI 호환 목적)
- 이모지/Unicode chars는 normalize 단계에서 제거 또는 변환
- icons 메타데이터는 별도 필드, 이름에 포함 금지

### 3.3 Alias Record (tool_aliases 별도 테이블)
```json
{
  "id": "tali_...",
  "service_tool_id": "tool_...",
  "exposed_name": "github.create_issue",
  "is_primary": true,
  "deprecated_at": null
}
```

slug rename 시:
1. 기존 primary alias → is_primary=false, deprecated_at=now()
2. 새 exposed_name으로 신규 alias INSERT
3. 2주 grace 동안 deprecated alias도 lookup 성공
4. 2주 후 cleanup job

## 4. tools/list Build Algorithm

```python
def build_tools_list(user_id: str, toolbox_id: str, cursor: str | None = None) -> ToolsListResult:
    toolbox = get_toolbox(user_id, toolbox_id)
    items = get_enabled_toolbox_items(toolbox.id)
    tools = []

    for item in items:
        service = get_active_service(item.service_id)
        if not service:
            continue
        if not policy.can_list_service(user_id, service.id):
            continue
        service_tools = get_active_cached_tools(service.id)
        for st in service_tools:
            if not policy.can_list_tool(user_id, st.id):
                continue
            alias = get_primary_alias(st.id)
            tools.append(to_exposed_tool(st, service, alias))

    sorted_tools = sort_by(tools, "exposed_name")
    return paginate(sorted_tools, cursor=cursor, limit=100)
```

### 4.0 tool → response mapping (MCP 2025-11-25)

`to_exposed_tool(st, service, alias)`는 다음 구조로 변환한다.

```python
{
  "name": alias.exposed_name,
  "title": service_tool.title,
  "description": service_tool.description,
  "icons": service_tool.icons_json,        # top-level, [{src, mimeType, sizes?}] 형식 (MCP 2025-11-25, ADR-029)
  "inputSchema": service_tool.input_schema_json,
  "outputSchema": service_tool.output_schema_json,
  "annotations": service_tool.annotations  # destructive/readOnly/idempotent/openWorld 등
}
```

**중요**: `icons`는 tool 객체의 **top-level** 필드이며 `annotations` 안에 위치하지 않는다 (ADR-029).

### 4.1 Cache Layer
- L1: in-process dict, key `user:{user_id}`, TTL 60s
- L2: in-memory dict 또는 Redis(옵션), TTL 1h
- L3: DB service_tools, TTL 24h

invalidation: 단일 프로세스는 직접 함수 호출. 다중 프로세스는 Redis pub/sub.

### 4.2 Sorting
deterministic: `service_slug ASC, exposed_name ASC`.

### 4.3 Empty Toolbox
MVP 권장: 빈 tools 배열 반환. Web UI에서 "MCP를 추가하세요" 안내.

옵션: `core.list_available_services`, `core.open_dashboard` 같은 helper tool 노출 (Phase P3+).

### 4.4 Pagination
- 기본 limit 100, 최대 500
- cursor: opaque (base64 of `{offset: N}`)
- nextCursor가 null이면 끝

## 5. tools/call Routing Algorithm

Error 분류 정책 (ADR-034):
- alias 없음 → -32602 (JSON-RPC error, result.isError 아님)
- arguments가 schema에 부적합 → -32602
- downstream tools/call이 isError=true 반환 → 그대로 forward

```python
async def call_tool(
    user_id: str,
    toolbox_id: str,
    exposed_name: str,
    arguments: dict,
    request_id: str,
    idempotency_key: str | None = None,
    cancellation_event: asyncio.Event | None = None,
) -> ToolResult:

    # 0. Idempotency
    if idempotency_key:
        cached = await idempotency_cache.get(user_id, idempotency_key)
        if cached:
            return cached

    # 1. Alias 조회 (user_id scope)
    alias = find_alias_by_exposed_name_for_user(user_id, exposed_name)
    if not alias:
        # ADR-034: unknown tool은 JSON-RPC -32602
        return json_rpc_error(-32602, "Unknown tool", coremcp_code="tool_not_found")

    # 2. Toolbox membership
    if not toolbox_contains_service(toolbox_id, alias.service_id):
        return tool_error("tool_not_in_toolbox")

    # 3. Service status
    service = get_service(alias.service_id)
    if service.status != "active":
        return tool_error("service_disabled")

    # 4. Policy
    if not policy.can_call_tool(user_id, alias.service_tool_id, arguments):
        return tool_error("policy_denied")

    # 5. Credential
    credential = resolve_credential(user_id, service.id)
    if service.auth_type != "none" and not credential:
        return tool_error("service_not_connected")

    # 6. Downstream request
    request = build_downstream_tools_call(
        service=service,
        downstream_tool_name=alias.service_tool.original_name,
        arguments=arguments,
        credential=credential,
        request_id=request_id,
    )

    # 7. Execute
    try:
        result = await downstream_mcp_client.call(
            request,
            cancellation_event=cancellation_event,
        )
    except asyncio.CancelledError:
        invocation_log(status="cancelled")
        return tool_error("cancelled")
    except DownstreamTimeoutError:
        return tool_error("downstream_timeout")
    except DownstreamProtocolError as e:
        return tool_error("downstream_error", downstream_code=e.code)

    # 8. Normalize
    normalized = normalize_result(result)
    # icons size cap 검증 (32KB after sanitize)
    # SVG는 sanitize 후 reject 가능, 그 경우 icons 배열에서 해당 entry 제외

    # 9. Idempotency cache
    if idempotency_key:
        await idempotency_cache.set(user_id, idempotency_key, normalized, ttl=86400)

    # 10. Log
    invocation_log(status="success", ...)
    return normalized
```

### 5.1 단일 사용자 단순화
- `user_id`는 사실상 `usr_local` 고정
- policy는 deny-by-default 없이 기본 allow + scanner risk_level high만 confirm

## 6. Downstream MCP Interaction

### 6.1 Initialize
Validation 시점:
```json
{
  "jsonrpc": "2.0",
  "id": "val-1",
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": { "name": "CoreMCP", "version": "1.0.0" }
  }
}
```

### 6.2 tools/list
```json
{ "jsonrpc": "2.0", "id": "val-2", "method": "tools/list", "params": {} }
```

다수의 cursor가 있으면 모든 페이지 fetch.

### 6.3 tools/call
```json
{
  "jsonrpc": "2.0",
  "id": "proxy-req_123",
  "method": "tools/call",
  "params": {
    "name": "create_issue",
    "arguments": { ... }
  }
}
```

### 6.4 Capabilities Forwarding
CoreMCP는 downstream initialize 결과의 capabilities를 service별 `mcp_services.capabilities_json`에 저장하고, client initialize 응답에서는 default toolbox active service의 capability union을 반환한다. `tools`는 CoreMCP gateway 기본 capability로 유지하고, `resources`/`prompts`는 지원 service가 있을 때만 노출한다.

CoreMCP가 downstream에 보내는 client capabilities는 gateway 자체 capabilities만:
```json
{
  "name": "CoreMCP Proxy",
  "version": "1.0.0",
  "capabilities": {
    "roots": null,
    "sampling": null,
    "elicitation": null
  }
}
```

downstream → CoreMCP `sampling/createMessage` / `elicitation/create` / `roots/list` 요청 시: -32601 반환 (Phase P3+ 결정 보류).

Tools call guard:
- catalog의 `input_schema_json`으로 `tools/call.params.arguments`를 JSON Schema 사전 검증한다.
- 실패 시 downstream 호출 없이 JSON-RPC `-32602 Invalid tool arguments`와 `policy.invalid_args` audit를 남긴다.
- HTTP downstream에는 안전 헤더로 `Idempotency-Key`를 forward한다.
- service별 fixed-window quota 초과 시 tool-level `rate_limited` 오류로 응답한다.

client → CoreMCP `tasks/*` / `sampling/createMessage` / `elicitation/create` / `roots/list` 요청:
- MVP: -32601 Method not found 반환
- Phase P3+: client capability에 따라 forward 또는 reject (ADR-029)

### 6.5 2025-11-25 추가 처리

- **icons metadata**: MCP 2025-11-25 tool **top-level** optional field. CoreMCP는 downstream에서 받은 icons를 `service_tools.icons_json` 컬럼에 저장 (05-database-schema §6.2). tools/list에서 tool의 top-level `icons` 필드로 forward. annotations 안에 두지 않는다 (ADR-029).

  icons object 구조 (MCP 2025-11-25 표준, HTML `<img>` 표준 align):
  ```json
  {
    "src": "https://github.com/icon.svg",
    "mimeType": "image/svg+xml",
    "sizes": "48x48"
  }
  ```

  - size cap: 32KB per tool
  - content-type allowlist: `image/png`, `image/svg+xml`, `image/webp`
  - URL 또는 data URI 모두 허용
  - URL 형태면 등록 시 SSRF guard 통과 + 캐시 옵션 (P1+)

  **SVG icon 보안 정책 (P2)**:
  - service 등록 시 icons content-type 검증
  - `image/svg+xml`는 검증 후 sanitize 저장:
    - `<script>` 태그 제거
    - `<foreignObject>` 제거 (HTML injection 방지)
    - `on*` 이벤트 핸들러 속성 제거
    - `<use href="http*">` 외부 참조 제거
    - `xlink:href`의 외부 URL 제거
  - sanitize 라이브러리: `bleach` 또는 `defusedxml.lxml` 권장
  - 옵션 환경 변수 `ICON_SVG_ENABLED=false` (default false 권장)로 SVG 완전 차단
  - data URI SVG도 동일 sanitize 적용
  - size cap 32KB는 sanitize 후 기준
  - sanitize 후 invalid 또는 잔여 unsafe 노드가 있으면 해당 icon entry는 `icons` 배열에서 drop
- **tasks 실험**: MCP 2025-11-25의 experimental method. **client가 CoreMCP에 `tasks/*` method를 요청**하면 CoreMCP는 `-32601 Method not found` 응답. downstream에서 받은 tasks 결과를 forward하지 않으며, downstream에 tasks/* 요청도 보내지 않는다. Phase P3+ 검토 (ADR-029).
- **JSON Schema dialect**: downstream의 inputSchema/outputSchema에 `$schema` 명시되어 있으면 그대로 forward, 없으면 2020-12 가정
- **input validation error**: downstream이 result.isError=true로 반환한 입력 검증 오류는 wrapping 없이 그대로 forward

## 7. Session Strategy

### 7.1 CoreMCP-Client Session
- Mcp-Session-Id 생성 (uuid4)
- in-memory dict (단일 프로세스)
- expiry 24h, last_seen sliding

### 7.2 Downstream Session
- 단일 사용자라 user_id 의미 작지만 cache key는 user_id 포함
- key: `downstream:session:service:{service_id}:user:{user_id}:cred:{cred_hash}`
- TTL 10분, 사용 시 sliding
- credential 변경 시 hash 변경 → 자동 invalidate

### 7.3 옵션: per-call initialize
session cache 미사용 시 매번 initialize. 단순하지만 느림.

## 8. Error Mapping

### 8.1 Tool-level Error (result.isError=true)
사용자가 고칠 수 있는 에러:
```json
{
  "content": [{"type": "text", "text": "..."}],
  "isError": true,
  "_meta": {
    "coremcp": {
      "error_code": "service_not_connected",
      "connect_url": "http://localhost:3000/services/github/credential"
    }
  }
}
```

### 8.2 JSON-RPC Error
| 상황 | code |
|---|---|
| unknown method | -32601 |
| invalid params | -32602 |
| parse error | -32700 |
| internal error | -32603 |
| invalid request | -32600 |

### 8.3 Mapping Table

| coremcp_error_code | JSON-RPC code | HTTP | 위치 |
|---|---|---|---|
| auth_required | n/a | 401 | HTTP layer |
| invalid_token | n/a | 401 | HTTP layer |
| tool_not_found | -32602 | 200 | error |
| invalid_arguments | -32602 | 200 | error |
| tool_not_in_toolbox | n/a (isError) | 200 | result.isError |
| service_disabled | n/a (isError) | 200 | result.isError |
| service_not_connected | n/a (isError) | 200 | result.isError |
| credential_expired | n/a (isError) | 200 | result.isError |
| policy_denied | n/a (isError) | 200 | result.isError |
| downstream_timeout | n/a (isError) | 200 | result.isError |
| downstream_error | n/a (isError) | 200 | result.isError |
| schema_stale | n/a (isError) | 200 | result.isError |
| cancelled | n/a (isError) | 200 | result.isError |
| method_not_found | -32601 | 200 | error |
| parse_error | -32700 | 400 | error |
| internal_error | -32603 | 200 | error |
| rate_limited | n/a | 429 | HTTP layer |
| body_too_large | n/a | 413 | HTTP layer |

## 9. Tool Catalog Freshness

### 9.1 States
- fresh
- stale (TTL 초과지만 사용 가능)
- missing (cache 없음)
- error (validation 실패)

### 9.2 Behavior

| State | tools/list | tools/call |
|---|---|---|
| fresh | include | allow |
| stale | include + background refresh | allow |
| missing | exclude | reject (service_not_connected) |
| error | exclude with _meta reason | reject |

## 10. Policy Hooks

단일 사용자라 정책이 거의 무. 그래도 hook point는 유지:
```text
before_tools_list_service
before_tools_list_tool
before_tool_call
before_downstream_request
after_downstream_response
```

MVP 구현:
```python
class PolicyChecker:
    def can_list_service(user, service) -> bool: return service.status == "active"
    def can_list_tool(user, tool) -> bool: return tool.status == "active"
    def can_call_tool(user, tool, args) -> bool:
        if tool.risk_level == "critical":
            return False  # 본인 설정에 따라
        return True
```

## 11. Observability

각 proxy call 기록:
```text
request_id
invocation_id
user_id
external_connection_id
toolbox_id
service_id
service_tool_id
exposed_tool_name
downstream_tool_name
status
latency_ms
downstream_latency_ms
error_code
input_size_bytes
output_size_bytes
protocol_version
idempotency_key
```

## 12. listChanged Emission

다음 경우 active SSE 채널에 `notifications/{tools,resources,prompts}/list_changed` emit:
1. toolbox 변경 (item add/remove/enable/disable) → `tools/list_changed`
2. tool permission/preset 변경 → `tools/list_changed`
3. service status 변경 (active ↔ disabled/error) → `tools/resources/prompts/list_changed` broadcast
4. service tool/resource/prompt catalog 변경 (validation 후) → `tools/resources/prompts/list_changed` broadcast
5. credential 변경 → service reachable 상태 변경 시 catalog invalidation

구현:
- domain event 발생 시 in-process event bus 호출
- 단일 프로세스: 직접 SSE handler 호출
- 최근 event ring buffer로 `Last-Event-Id` reconnect backfill 지원
- downstream HTTP `text/event-stream` response와 STDIO JSON-RPC notification line에서 `notifications/progress`, `notifications/resources/updated`, `notifications/{tools,resources,prompts}/list_changed` fan-out
- emission 빈도 제한: 1초당 1회 debounce

Resource routing:
- active service가 하나 이상 있으면 `resources/read`는 toolbox catalog에 존재하는 URI만 해당 service로 라우팅한다.
- catalog miss 또는 동일 URI가 여러 active service에 존재하는 ambiguous 상태는 downstream broadcast/first-hit 대신 `-32602 Unknown resource`로 실패한다.

icons 변경(service refresh 후 icons_json 갱신)은 schema_hash 계산에 포함되지 않으면 listChanged emit 안 함. 단 schema_hash 계산에 icons 포함하면 emit 트리거. 권장: icons는 schema_hash 계산에 포함하지 않음 (LLM context에 영향 미미).

## 13. Compatibility Tests

- Claude Code initialize / tools/list / tools/call
- bearer auth 검증
- session id 발급 / 재사용 / 만료
- listChanged emission
- cancellation propagation
- idempotency_key 동작
- pagination cursor
- structured content 응답 보존
- annotations 보존
- protocol_version downgrade
- service_not_connected error
- schema drift detection
- 2025-11-25 initialize 협상
- 2025-06-18 initialize fallback
- unknown tool → -32602 응답
- malformed params → -32602
- downstream isError → result.isError forward
- icons metadata passthrough
- icons가 tools/list 응답에서 tool top-level로 노출됨
- icons가 annotations 안에 있지 않음
- icons size > 32KB 시 reject 또는 truncate
- icons content-type allowlist 외 reject
- tasks/* method downstream 요청 시 -32601
- icons object의 `src` field 사용 확인 (`url`이 아님)
- client가 tasks/* 요청 시 -32601 반환 (CoreMCP는 downstream에 forward 안 함)
- tool name 64자 cap은 CoreMCP 정책이며 spec hard cap이 아님을 명시
