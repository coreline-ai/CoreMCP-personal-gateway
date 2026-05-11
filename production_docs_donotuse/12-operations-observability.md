# CoreMCP Operations and Observability

문서 버전: v0.1

---

## 1. 운영 목표

CoreMCP는 외부 AI client와 downstream MCP service 사이에 위치한다. 장애 원인이 CoreMCP인지 downstream인지 빠르게 구분할 수 있어야 한다.

---

## 2. Key Metrics

### 2.1 Gateway Metrics

```text
mcp_requests_total{method,client_type,status}
mcp_request_duration_ms{method,client_type}
mcp_auth_failures_total{reason}
mcp_sessions_active
mcp_tools_list_count{user,toolbox}
mcp_tools_call_count{service,tool,status}
```

### 2.2 Downstream Metrics

```text
downstream_requests_total{service,status}
downstream_latency_ms{service}
downstream_timeout_total{service}
downstream_auth_error_total{service}
downstream_schema_error_total{service}
```

### 2.3 Product Metrics

```text
users_signup_total
mcp_services_created_total
toolbox_items_added_total
external_connections_created_total
first_tool_call_success_total
validation_success_rate
dau_total
mau_total
retention_d1_d7_d30
activation_funnel_step_total{step}
churn_rate_monthly
customer_health_score
```

---

## 3. Logs

### 3.1 Request Log

Fields:

```json
{
  "timestamp": "...",
  "level": "info",
  "request_id": "req_...",
  "user_id": "usr_...",
  "external_connection_id": "ext_...",
  "method": "tools/call",
  "status": "success",
  "latency_ms": 820,
  "request_size_bytes": 1234,
  "response_size_bytes": 5678,
  "protocol_version": "2025-06-18",
  "client_type": "claude_code",
  "trace_id": "...",
  "span_id": "...",
  "region": "ap-northeast-2"
}
```

### 3.2 Invocation Log

Stored in DB plus emitted to observability pipeline.

Fields:

```text
request_id
invocation_id
user_id
service_id
exposed_tool_name
status
latency_ms
downstream_latency_ms
error_code
```

### 3.3 Redaction

Redact keys:

```text
authorization
cookie
set-cookie
api_key
apikey
token
refresh_token
access_token
password
secret
client_secret
```

---

## 4. Tracing

Trace spans:

```text
HTTP /mcp
  auth.validate_token
  mcp.parse_jsonrpc
  toolbox.resolve
  tool_alias.resolve
  policy.check
  credential.resolve
  downstream.call
  invocation.log
  db.query{operation,table}
  redis.command{command}
  kms.decrypt
  oauth.token_validate
  oauth.token_introspect
  cache.lookup{layer}
  policy.evaluate
```

OpenTelemetry semantic conventions 채용 (`http.*`, `db.*`, `messaging.*`).

---

## 5. Alerts

각 alert에는 actionable runbook 링크를 포함한다.

### P0 Alerts

- CoreMCP `/mcp` 5xx rate > 5% for 5 minutes → runbook §7.5
- auth validation unavailable → runbook §7.2
- database unavailable → runbook §7.7
- plaintext secret detection in logs → runbook §7.3
- SSRF guard failure event → runbook §7.4

### P1 Alerts

- tools/call timeout rate > 10% for 10 minutes → runbook §7.1
- validation failure rate > 50% for 30 minutes → runbook §7.1
- downstream service high error for popular service → runbook §7.1
- queue lag > 5 minutes → runbook §7.7

### P2 Alerts

- tools/list p95 > 1s → runbook §7.1
- stale cache ratio > 30% → runbook §7.1
- one-time token exchange failure spike → runbook §7.2

### P0 Alerts (추가)

- KMS API error rate > 1% for 1m → runbook §7.5 (R-021)
- OAuth issuer JWKS fetch fail for 5m → runbook §7.2
- refresh token family revoke event > 10/h → R-013 (audit alert)
- RLS policy bypass 검출 → 즉시 incident
- right_to_erasure job stuck > 24h → 16-compliance.md §4.3

---

## 6. Health Checks

### GET /health

```json
{
  "status": "ok"
}
```

### GET /ready

Checks:

- DB connection
- Redis connection
- KMS/Vault access
- auth metadata fetch/cache

### GET /live

Process liveness only.

---

## 7. Runbooks

### 7.1 Downstream Service Failing

Symptoms:

- `downstream_error_total` spike
- specific service timeout

Actions:

1. Check service health page.
2. Check latest validation report.
3. Trigger schema refresh.
4. Mark service degraded if repeated failure.
5. Do not disable entire CoreMCP.

### 7.2 Auth Failures Spike

Actions:

1. Check issuer metadata availability.
2. Check JWKS cache.
3. Check clock skew.
4. Check audience/resource changes.
5. Roll back auth config if needed.

### 7.3 Suspected Credential Leak

Actions:

1. Revoke affected secret_ref.
2. Disable affected service connection.
3. Notify user/workspace owner.
4. Rotate KMS data key if needed.
5. Export audit logs.

### 7.4 SSRF Attempt

Actions:

1. Confirm blocked URL/IP.
2. Inspect user/service.
3. Rate limit or suspend if malicious.
4. Add pattern to blocklist.

### 7.5 KMS Unavailable

Symptoms:
- `kms.decrypt` span error rate spike
- tools/call에서 credential resolve fail

Actions:
1. AWS KMS console에서 region별 status 확인.
2. cached DEK 사용 mode 진입 (in-process 60s cache).
3. cross-region KMS replica로 failover (자동).
4. 1h 이상 지속 시 사용자 알림 + status page 업데이트.
5. R-021 mitigation 적용.

### 7.6 Auth Server Outage

Symptoms:
- Logto health check fail
- 신규 token 발급 불가

Actions:
1. Logto pod restart / replica scale-up.
2. JWKS cache TTL 연장 (기존 token은 valid).
3. introspection 대신 local JWT 검증만 사용.
4. 신규 로그인 차단 페이지 표시.

### 7.7 Database Failover

Symptoms:
- PostgreSQL primary 응답 불가

Actions:
1. RDS automatic failover 대기 (1-2min).
2. application connection pool reset.
3. read-replica로 read 트래픽 일시 라우팅 (config flag).
4. write 작업 결제 큐로 buffering (Phase 3+).
5. PITR 검토 (data corruption 의심 시).

### 7.8 Right-to-Erasure Execution

When:
- user.delete_request 후 30일 경과
- 또는 user 즉시 삭제 요청 (legal)

Actions:
1. job queue에 erasure task 등록.
2. user_id로 mcp_services / toolboxes / external_connections cascade soft-delete.
3. audit_logs / tool_invocations의 actor_user_id NULL anonymize.
4. KMS ciphertext destroy (secret_ref 가리키는 모든 항목).
5. 다음 backup cycle 후 자연 삭제.
6. 16-compliance.md §14 절차 준수.
7. user에게 완료 통지 email.

---

## 8. Backups

- PostgreSQL: daily full + WAL archiving (PITR enabled)
- RPO: 5분 (PITR)
- RTO: 30분 (single region failover) / 4시간 (cross-region disaster, Phase 5+)
- Redis: not source of truth, snapshot daily for analytics
- KMS: managed by AWS, cross-region replica enabled
- Backup encryption: SSE-KMS (별도 backup key)
- Backup retention: 35일 (daily), 1년 (monthly), 7년 (yearly tax/audit)
- Restoration test: 분기 1회 dry-run

---

## 9. SLO Draft

| SLO | Target |
|---|---|
| `/mcp` availability | 99.5% MVP |
| tools/list latency p95 | < 500ms |
| gateway overhead tools/call p95 | < 150ms |
| successful validation job completion | > 95% for healthy downstream |
| audit log write success | > 99.9% |
| SLO 6: signup → first tool call success rate | > 60% within 24h (activation) |
| SLO 7: refresh token rotation success | > 99.95% |
| SLO 8: KMS decrypt latency p95 | < 100ms |
| SLO 9: SSE notification delivery | < 5s end-to-end |
| SLO 10: right-to-erasure completion | < 24h after grace period |

Error budget burn rate alerts:
- 2% burn in 1h → P1 alert
- 5% burn in 1h → P0 alert
- monthly budget 50% by mid-month → engineering review

---

## 10. Dashboard Views

### Engineering Dashboard

- request rate
- error rate
- latency
- downstream error by service
- queue lag
- DB/Redis health

### Product Dashboard

- signups
- MCP services registered
- toolbox adds
- external connections
- tool calls per user
- activation funnel

### Security Dashboard

- auth failures
- SSRF blocks
- token audience invalid
- credential rotations
- public submission warnings
- tool metadata scan critical findings

### Cost Dashboard
- KMS API call count + estimated $/day
- DB IOPS + storage growth rate
- Egress bandwidth per region
- downstream call count per service (proxy for downstream API cost)
- Sentry/OTel ingestion cost

### Customer Success Dashboard
- per-customer error rate (p99 tool_invocation status=error)
- per-customer support ticket count
- per-customer feature adoption (toolbox count, external_connection count)
- churn risk score (declining usage trend)
- NPS / CSAT (Phase 3+)

### Compliance Dashboard
- right-to-erasure pending count
- audit log export request count
- data residency violation events
- subprocessor breach notification (manual entry)

---

## 11. Incident Response

### 11.1 Severity Matrix

| Sev | 정의 | 응답시간 | 통지 |
|---|---|---|---|
| SEV1 | 전체 서비스 down 또는 보안 침해 | 15분 | CEO/CTO + status page |
| SEV2 | 주요 기능 영향 (tools/call 25%+ fail) | 30분 | engineering lead + status page |
| SEV3 | 부분 기능 또는 단일 사용자 | 4시간 | 담당자 |
| SEV4 | cosmetic, planned | 1주 | normal |

### 11.2 On-Call Rotation
- engineer 2명 primary/secondary weekly
- PagerDuty 또는 Opsgenie
- runbook 링크 alert에 포함

### 11.3 Escalation
- SEV1: 15분 응답 없으면 secondary, 30분 추가 시 lead, 1시간 시 CEO

---

## 12. Postmortem Template

```
## Incident YYYY-MM-DD-NNN

- **Severity**: SEV1/2/3/4
- **Duration**: HH:MM → HH:MM (XX min)
- **Impact**: 영향받은 user/feature/region
- **Detected**: monitoring/customer report/internal
- **Resolved by**: 조치 내용

## Timeline
- HH:MM detection
- HH:MM diagnosis
- HH:MM mitigation
- HH:MM resolution

## Root Cause
- 5-why

## What went well
## What went poorly
## Action items
- [ ] owner / deadline / severity
```

postmortem 공개 정책: SEV1/2는 internal + sanitized public (status page blog).

---

## 13. Deploy Strategy

- production: blue/green (Render/Fly app slots)
- canary: 5% traffic 30min → 100%
- feature flag (PostHog / Unleash) for risky changes
- rollback: 1-click 또는 git revert + redeploy
- DB migration: online only (gh-ost 패턴)

## 14. Status Page

- statuspage.io 또는 자체
- 주요 component: API, MCP Gateway, OAuth AS, Downstream proxy
- incident 자동 alert → status page 자동 업데이트 (검토)
- subscriber notification: email, webhook, RSS

## 15. Database Migration Strategy

- expand-contract pattern
- step 1: 새 컬럼 추가 (nullable)
- step 2: app code 양방향 호환
- step 3: backfill
- step 4: 새 컬럼 NOT NULL 또는 old 컬럼 drop
- 각 step deploy 분리, rollback 안전

## 16. Cost Observability

per-customer cost estimation:
- KMS API call * $0.03/10K
- DB connection * time (RDS pricing)
- Egress bandwidth
- downstream call (heuristic — actual은 customer 측 외부 API 청구)

월간 customer cost report (internal):
- workspace_id별 estimated infrastructure cost
- ROI 분석 (Pro plan 사용자 vs cost)
