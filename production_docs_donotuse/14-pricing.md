# CoreMCP Pricing Model

문서 버전: v0.1
작성일: 2026-05-11
제품 유형: MCP Toolbox + Authenticated MCP Gateway SaaS

---

## 1. Pricing 목표

CoreMCP의 pricing은 다음 세 가지 원칙으로 설계한다.

- developer adoption은 Free tier로 확보한다. 개인 개발자가 신용카드 없이 가입 후 즉시 Claude Code/ChatGPT에 toolbox를 붙일 수 있어야 한다.
- monetization은 Team workspace + audit/SSO/quota에서 발생한다. 단일 사용자 가치보다 팀 협업, 컴플라이언스, 운영 가시성에서 결제 가치를 만든다.
- transparent, usage-based, predictable. 가격표는 공개하고, 과금 metric은 사용자 대시보드에서 실시간으로 확인 가능해야 하며, 월 사용량 급증으로 인한 예측 불가능한 청구를 만들지 않는다.

---

## 2. Plan 구조 (4-tier)

| Plan | 가격 | 대상 | 핵심 제한 |
|---|---|---|---|
| Free | $0 | 개인 개발자, 학습, 평가 | 1 toolbox, 3 services, 1K tools/call/month, 90d 로그 |
| Pro | $9-19/mo | 파워 유저, 1인 개발자, 프리랜서 | 5 toolbox, 20 services, 50K tools/call, 365d 로그, OAuth-delegated downstream |
| Team | $29/seat/mo | 5+ 명 팀, 스타트업 | unlimited toolbox/services, 500K tools/call, workspace, RBAC, audit export, SSO |
| Enterprise | custom | 대기업, 규제 산업, 공공 | SLA, BYOK, dedicated tenant, IP allowlist, SOC2 report, on-prem option |

Free → Pro → Team → Enterprise는 단조 증가 구조이며 downgrade 시 quota는 다음 청구 주기부터 적용한다.

---

## 3. Quota 매트릭스 상세

| Quota Item | Free | Pro | Team | Enterprise |
|---|---|---|---|---|
| toolboxes 개수 | 1 | 5 | unlimited | unlimited |
| mcp_services 등록 개수 | 3 | 20 | unlimited | unlimited |
| toolbox_items 개수 (per toolbox) | 20 | 100 | unlimited | unlimited |
| service_tools 캐시 개수 | 100 | 1,000 | 10,000 | unlimited |
| tools/call per month | 1,000 | 50,000 | 500,000 / workspace | custom (계약) |
| tools/list per minute (rate limit) | 10 | 60 | 300 | custom |
| one-time connection token per hour | 5 | 30 | 200 | custom |
| external_connections 동시 활성 개수 | 2 | 10 | 50 / workspace | custom |
| service validation per day | 20 | 200 | 2,000 | custom |
| audit log retention (days) | 90 | 365 | 730 | custom (>=730) |
| tool_invocation retention (days) | 30 | 180 | 365 | custom |
| downstream call timeout | 15s | 30s | 30s | custom (max 60s) |
| response body max size | 1 MB | 5 MB | 5 MB | custom (max 25 MB) |

quota 초과 시 동작은 `06-security-auth.md`의 rate limit/quota enforcement 규칙을 따른다. 기본은 429 응답이며 hard cap 도달 시 다음 주기 시작까지 차단한다.

---

## 4. Free → Paid 전환 트리거

Free 사용자가 다음 조건에 도달하면 in-app/email로 업그레이드 안내를 전송한다.

- Free의 monthly tool call 80% 도달 시 in-app 알림
- Free에서 OAuth-delegated downstream 시도 시 Pro 안내 (Free는 API key/static credential downstream만 허용)
- Free에서 workspace 생성 시도 시 Team 안내
- 첫 invocation 후 14일 trial Pro 자동 활성화 (검토)
- Free에서 audit log export 시도 시 Team 안내
- Free에서 SSO 설정 진입 시 Team 안내

알림은 무료 사용자 경험을 해치지 않도록 30일 내 중복 발송을 제한한다.

---

## 5. Billing

- 결제: Stripe (subscription + metered usage)
- 통화: USD 우선, KRW 옵션 (검토)
- 청구 주기: monthly / annual (annual 17% 할인)
- 환불: 7일 미사용 환불 보장 (Pro), Team/Ent은 prorated
- 세금: Stripe Tax 위임 (VAT/GST/한국 부가세 포함)
- 결제 수단: Stripe 지원 카드, ACH (US), SEPA (EU); Enterprise는 invoice/wire 가능
- 영수증/세금계산서: Stripe Invoice + 한국 사용자 대상 별도 세금계산서 발행 (검토)

청구는 plan 기본료 + metered usage overage 합산이며 사용자는 dashboard `/billing`에서 실시간 누적치를 확인할 수 있다.

---

## 6. Usage Metering

다음 metric을 monthly aggregated하여 청구한다.

- tool_invocation_count (per workspace)
- mcp_service_active_count
- external_connection_active_count
- audit_log_storage_gb (Team+)

`billing_usage_counters` 테이블에 daily snapshot을 적재하고 monthly close 시점에 invoice generation을 트리거한다.

```text
billing_usage_counters
  workspace_id        uuid
  metric_name         text   -- tool_invocation | mcp_service_active | external_conn_active | audit_log_gb
  period_date         date
  counter_value       bigint
  snapshot_at         timestamptz
  PRIMARY KEY (workspace_id, metric_name, period_date)
```

monthly close 작업은 매월 1일 KST 00:30 cron으로 실행되며 전월 사용량을 Stripe usage record로 push한다.

---

## 7. Fair Use Policy

모든 plan에 공통 적용되는 fair use 규칙은 아래와 같다.

- 단일 tool call에 30s timeout, 5MB response (plan별 quota 매트릭스에 따름)
- 분당 60 tools/call 초과 시 429 응답 (Free는 분당 10)
- abuse 의심 시 24h 내 quota 자동 동결, 사용자에 통지
- shared infra 부하를 일으키는 패턴(loop invocation, infinite redirect, prompt injection 기반 탐색)은 사전 통지 없이 차단 가능

abuse detection은 `12-operations-observability.md`의 anomaly detection 규칙을 재사용한다.

---

## 8. Open Marketplace 수익 분배 (Phase 4+)

Marketplace는 MVP 범위가 아니며 Phase 4 이후 도입을 검토한다.

- 개발자 등록 MCP가 paid plan에서 사용될 경우 revenue share
- 기본: 70% developer / 30% CoreMCP (검토)
- payment processor: Stripe Connect (검토)
- payout 주기: monthly, 최소 $50부터 (검토)
- developer KYC: Stripe Connect Standard 위임

수익 분배 metric은 `tool_invocation_count` × `tool_unit_price` 기반이며 marketplace 등록 시 개발자가 단가를 설정한다.

---

## 9. Enterprise 특별 사항

Enterprise plan은 다음 조건을 포함한다.

- annual contract minimum: $30K
- DPA + Subprocessor 계약
- BYOK (Bring Your Own Key) — AWS KMS cross-account
- SCIM 2.0 user provisioning
- audit log SIEM stream (CloudWatch / Datadog / Splunk)
- 99.9% uptime SLA, credit 정책 포함
- dedicated support channel (Slack Connect 또는 전용 email)
- 분기별 service review

on-prem 옵션은 별도 license 계약으로 처리하며 docker-compose / Helm chart 형태로 제공한다.

---

## 10. 무료 사용자 보호

Free tier 사용자는 paid 사용자와 동일한 보안 기준으로 보호된다.

- Free user 데이터도 동일 보안 기준 (KMS 암호화, RLS, audit)
- Free 계정 비활성 90일 후 알림, 180일 후 데이터 삭제 옵트인
- Free → Pro 전환 시 기존 데이터/로그 무손실 마이그레이션
- Free 사용자도 personal data export API 사용 가능 (GDPR/PIPA 준수)

---

## 11. Pricing 운영 결정

- 가격 공개 vs custom quote: Free/Pro/Team 공개, Enterprise custom
- 결제 페이지: `/pricing`
- billing portal: Stripe Customer Portal 통합
- plan 변경 UX: dashboard `/billing/plan`에서 즉시 upgrade, downgrade는 다음 주기 적용
- failed payment dunning: Stripe Smart Retries + 7일 grace period 후 read-only mode 전환
- read-only mode: tool 등록/수정 불가, 기존 tool 호출은 quota 내 유지

---

## 12. KPI

| Metric | Target | 측정 주기 |
|---|---|---|
| Free → Pro conversion | 5% (12개월 내) | monthly cohort |
| Pro → Team upgrade | 15% | quarterly |
| monthly churn (Pro) | < 5% | monthly |
| monthly churn (Team) | < 2% | monthly |
| LTV / CAC | > 3x | quarterly |
| NRR (Net Revenue Retention) | > 110% (Team+) | quarterly |
| ARPU (Pro) | $14 | monthly |
| ARPA (Team) | $250 | monthly |

KPI는 `12-operations-observability.md`의 business metrics dashboard에서 집계한다.

---

## 13. Open Questions

1. KR 사용자 가격 할인 또는 동일?
2. annual prepay 할인 폭(17% vs 20%)?
3. Open source 프로젝트 sponsorship discount?
4. Education plan?
5. 처음 90일 사용자에게 Pro trial 자동 부여?
6. Marketplace 수익 분배 70/30이 적정한가?
7. Enterprise BYOK 외 BYO-Logto / BYO-OAuth-AS 옵션?

해당 항목은 private beta 종료 후 사용자 인터뷰 결과를 기반으로 v0.2에서 확정한다.
