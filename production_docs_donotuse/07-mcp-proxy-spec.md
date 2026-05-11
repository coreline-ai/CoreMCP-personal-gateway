# CoreMCP MCP Proxy Specification

문서 버전: v0.1

---

## 1. 목적

이 문서는 CoreMCP가 하나의 MCP server처럼 보이면서 내부적으로 여러 downstream MCP service의 tool을 proxy하는 방법을 정의한다.

---

## 2. Proxy Mode

CoreMCP는 meta-tool 방식이 아니라 proxy mode를 사용한다.

금지/비권장:

```text
invoke_tool(service, tool, args)
```

권장:

```text
github.create_issue
notion.search_page
calendar.create_event
```

이유:

- LLM이 tool을 자연스럽게 선택한다.
- Claude Code/Claude/ChatGPT에서 tool catalog가 명확히 보인다.
- PlayMCP형 toolbox UX와 맞다.

---

## 3. Tool Name Mapping

### 3.1 Exposed Tool Name

기본 형식:

```text
{service_slug}.{normalized_tool_name}
```

예:

```text
github.create_issue
notion.search_page
kakao_chat.send_message
```

형식 확정: `{service_slug}.{tool_name}` (ADR-019)
- service_slug: lowercase, [a-z0-9-]+, 3-32 chars
- tool_name: lowercase, [a-z0-9_]+, 1-48 chars
- 총 길이 64자 이내 (보수적 client 호환)
- dot 처리 비호환 client 발견 시 underscore fallback per-client profile (17-mcp-client-profiles.md)

### 3.2 Normalization Rules

입력: downstream tool name

처리:

1. trim
2. Unicode NFKC normalize
3. lowercase 강제 (원본 이름은 service_tools.original_name 보존)
4. spaces → `_`
5. unsupported chars → `_`
6. multiple `_` collapse
7. zero-width / RTL override 제거
8. max length 48 chars (slug 포함 64자 한계)
9. reserved names reject

Reserved:

```text
core.*
admin.*
internal.*
mcp.*
_meta.*
```

### 3.3 Alias Record

alias는 별도 tool_aliases 테이블 (ADR-016, 05-database-schema.md §5.4).

```json
{
  "id": "tali_...",
  "service_tool_id": "tool_123",
  "exposed_name": "github.create_issue",
  "is_primary": true,
  "deprecated_at": null
}
```

service slug rename 시:
1. 기존 primary alias → is_primary=false, deprecated_at=now()
2. 새 exposed_name으로 신규 alias INSERT
3. 사용자에게 2주간 deprecated alias도 작동 보장
4. 2주 후 cleanup job

---

## 4. tools/list Build Algorithm

Cache layer (ADR-018):
- L1: in-process LRU per pod, key=user_id, TTL 60s
- L2: Redis, key=`cache:catalog:user:{user_id}`, TTL 1h
- L3: PostgreSQL service_tools, TTL 24h

Invalidation:
- toolbox 변경 → L1/L2 invalidate per user (pub/sub)
- service tool refresh → L2 invalidate per affected user (toolbox_items 역조회)
- credential 변경 → 동일

Pseudo-code:

```python
def build_tools_list(user_id: str, toolbox_id: str) -> list[Tool]:
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
            tools.append(to_exposed_tool(st, service))

    return sort_by_name(tools)
```

### 4.1 Sorting

Tool order는 deterministic해야 한다.

권장:

```text
service_slug asc, exposed_name asc
```

### 4.2 Empty Toolbox

빈 도구함이면 다음 중 하나를 반환한다.

Option A: empty tools

```json
{ "tools": [] }
```

Option B: CoreMCP helper tools

```text
core.list_available_services
core.open_toolbox_url
```

MVP 권장: Option A + client guide에서 웹 링크 제공.

---

## 5. tools/call Routing Algorithm

Pseudo-code:

```python
async def call_tool(user_id, toolbox_id, exposed_name, arguments,
                   request_id, idempotency_key=None, cancellation_event=None):
    # 0. idempotency check
    if idempotency_key:
        cached = await idempotency_cache.get(user_id, idempotency_key)
        if cached:
            return cached

    alias = find_alias_by_exposed_name_for_user(user_id, exposed_name)
    if not alias:
        return tool_error("tool_not_found")

    if not toolbox_contains_service(toolbox_id, alias.service_id):
        return tool_error("tool_not_in_toolbox")

    service = get_service(alias.service_id)
    if service.status != "active":
        return tool_error("service_disabled")

    if not policy.can_call_tool(user_id, alias.service_tool_id, arguments):
        return tool_error("policy_denied")

    credential = resolve_credential(user_id, service)
    if service.auth_type != "none" and not credential:
        return tool_error("service_not_connected")

    request = build_downstream_tools_call(
        service=service,
        downstream_tool_name=alias.original_name,
        arguments=arguments,
        credential=credential,
    )

    try:
        result = await downstream_mcp_client.call(
            request, cancellation_event=cancellation_event
        )
    except CancelledError:
        invocation_log(status="cancelled")
        return tool_error("cancelled")

    if idempotency_key:
        await idempotency_cache.set(user_id, idempotency_key, result, ttl=86400)

    return normalize_result(result)
```

Alias lookup은 user_id scope 강제 (cross-user alias collision 방지).

---

## 6. Downstream MCP Interaction

### 6.1 Downstream Initialize

CoreMCP validation worker는 service 등록 시 downstream MCP initialize를 호출한다.

Example:

```json
{
  "jsonrpc": "2.0",
  "id": "val-1",
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {
      "name": "CoreMCP Validator",
      "version": "0.1.0"
    }
  }
}
```

### 6.2 Downstream tools/list

```json
{
  "jsonrpc": "2.0",
  "id": "val-2",
  "method": "tools/list",
  "params": {}
}
```

### 6.3 Downstream tools/call

```json
{
  "jsonrpc": "2.0",
  "id": "proxy-req_123",
  "method": "tools/call",
  "params": {
    "name": "create_issue",
    "arguments": {
      "repo": "acme/app",
      "title": "Bug"
    }
  }
}
```

### 6.4 Capabilities Forwarding 정책

CoreMCP는 initialize에서 client capabilities를 받지만 downstream에는 자체 capabilities를 보낸다:

```json
{
  "name": "CoreMCP Proxy",
  "version": "0.1.0",
  "capabilities": {
    "roots": null,
    "sampling": null,
    "elicitation": null
  }
}
```

이유: ADR-014에 따라 sampling/elicitation reject. roots는 Phase 3 결정.

downstream → CoreMCP `sampling/createMessage` / `elicitation/create` / `roots/list` 요청:
- MVP: `-32601 Method not found` 반환
- Phase 3+: client capability에 따라 forward 또는 reject

---

## 7. Session Strategy

### 7.1 CoreMCP Client Session

CoreMCP는 external AI client와 별도 MCP session을 유지한다.

### 7.2 Downstream Session

MVP options:

Option A: stateless per call

- every validation/call starts initialize if downstream requires session
- 단순하지만 느림

Option B: short-lived downstream session cache

- service_id + user_id + credential_hash 기준 session reuse
- Redis TTL 10분
- 복잡하지만 효율적

Session cache key 규약 (ADR-018):
- key: `downstream:session:service:{service_id}:user:{user_id}:cred:{credential_hash}`
- user_id 포함 강제 — cross-user session reuse 방지
- credential 변경 시 hash 변경으로 자동 invalidate
- TTL 10분, 사용 시 sliding refresh

MVP 권장:

```text
Validation: per job session
Proxy call: per call initialize fallback, session cache optional
```

---

## 8. Error Mapping

### 8.1 CoreMCP Tool Error

사용자에게 고칠 수 있는 에러는 `isError: true` result로 반환한다.

예:

```json
{
  "content": [{"type": "text", "text": "This service is not connected."}],
  "isError": true,
  "_meta": {
    "coremcp_error_code": "service_not_connected"
  }
}
```

### 8.2 JSON-RPC Error

Protocol violation, invalid params 등은 JSON-RPC error 사용.

| 상황 | Error |
|---|---|
| unknown method | -32601 |
| invalid params | -32602 |
| parse error | -32700 |
| internal error | -32603 |

### 8.3 Downstream Error

Downstream JSON-RPC error는 다음으로 wrapping한다.

```json
{
  "content": [{"type": "text", "text": "Downstream MCP returned an error."}],
  "isError": true,
  "_meta": {
    "coremcp_error_code": "downstream_error",
    "downstream_error_code": -32000
  }
}
```

### 8.4 Error Code Mapping Table

| coremcp_error_code | JSON-RPC code | HTTP | 위치 |
|---|---|---|---|
| auth_required | n/a | 401 | HTTP layer |
| insufficient_scope | n/a | 403 | HTTP layer |
| tool_not_found | n/a (isError result) | 200 | result.isError |
| tool_not_in_toolbox | n/a (isError) | 200 | result.isError |
| service_disabled | n/a (isError) | 200 | result.isError |
| service_not_connected | n/a (isError) | 200 | result.isError |
| credential_expired | n/a (isError) | 200 | result.isError |
| policy_denied | n/a (isError) | 200 | result.isError |
| downstream_timeout | n/a (isError) | 200 | result.isError |
| downstream_error | n/a (isError) | 200 | result.isError |
| schema_stale | n/a (isError) | 200 | result.isError |
| cancelled | n/a (isError) | 200 | result.isError |
| invalid_arguments | -32602 | 200 | error |
| parse_error | -32700 | 400 | error |
| method_not_found | -32601 | 200 | error |
| internal_error | -32603 | 200 | error |
| rate_limited | n/a | 429 | HTTP layer |
| body_too_large | n/a | 413 | HTTP layer |
| protocol_version_unsupported | -32600 | 200 | error (initialize) |

---

## 9. Tool Catalog Freshness

### 9.1 Freshness States

```text
fresh
stale
missing
error
```

### 9.2 Behavior

| State | tools/list | tools/call |
|---|---|---|
| fresh | include | allow |
| stale | include with background refresh | allow |
| missing | exclude | reject |
| error | exclude with reason in `_meta` | reject with `service_not_connected` |

MVP 권장:

- stale cache는 include
- missing cache는 exclude

---

## 10. Policy Hooks

Policy hook points:

```text
before_tools_list_service
before_tools_list_tool
before_tool_call
before_downstream_request
after_downstream_response
```

MVP implementation:

```python
class PolicyChecker:
    def can_list_service(user, service): ...
    def can_list_tool(user, tool): ...
    def can_call_tool(user, tool, args): ...
```

---

## 11. Observability

각 proxy call은 다음 값을 기록한다.

```text
request_id
invocation_id
user_id
external_connection_id
toolbox_id
service_id
exposed_tool_name
downstream_tool_name
status
latency_ms
downstream_latency_ms
error_code
input_size_bytes
output_size_bytes
```

---

## 12. MVP Compatibility Tests

- Claude Code tools/list
- Claude Code tools/call
- Bearer token MCP service proxy
- No-auth MCP service proxy
- service_not_connected error
- stale tool cache behavior
- downstream timeout mapping
- invalid tool arguments mapping
- protocol_version downgrade negotiation
- structuredContent 응답 (2025-06-18)
- progress notification forward
- cancellation propagation
- listChanged emission 트리거 (toolbox 변경)
- session reuse cross-user 차단
- pagination cursor 동작
- annotation(destructive/readOnly/idempotent) 전달

---

## 13. listChanged Notification Emission

CoreMCP는 다음 경우에 활성 MCP session에 `notifications/tools/list_changed`를 emit한다:

1. user가 toolbox에 service 추가/제거
2. user가 toolbox item enable/disable
3. downstream service의 schema_hash 변경 (validation 후)
4. service status active ↔ disabled/error 전환
5. credential 변경으로 service reachable 상태 변경

구현:
- domain event 발생 → Redis pub/sub `events:user:{user_id}` 발행
- 각 pod의 SSE handler가 구독, 영향받은 user의 active session에 push
- emission 빈도 제한: 1초당 1회 (debounce)

미구현 시 client는 stale catalog 노출 → 02-trd.md §2.4 listChanged:true capability 선언과 모순.
