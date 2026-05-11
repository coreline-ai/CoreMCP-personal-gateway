# CoreMCP Compliance, Privacy, and Data Residency

문서 버전: v0.1
작성일: 2026-05-11

---

## 1. Compliance 목표
- private beta: 한국 개인정보보호법 준수
- public beta: GDPR/CCPA 대응 준비
- paid GA: SOC2 Type I, 12-18개월 내 Type II
- enterprise: ISO 27001 검토

## 2. Regulatory Scope

| 법규 | 적용 조건 | 대응 일정 |
|---|---|---|
| 개인정보보호법 (KR) | KR 사용자 시 필수 | private beta |
| GDPR (EU) | EU 사용자 가입 가능 시 | public beta |
| CCPA (US-CA) | CA 거주자 5만명+ 또는 매출 25M+ | revenue 기준 도달 시 |
| HIPAA (US) | healthcare MCP 시 | 명시적 BAA 체결 시 |
| PCI-DSS | 결제 데이터 직접 처리 시 | Stripe 위임으로 회피 |
| SOC2 | enterprise customer 요구 | paid GA + 6개월 |

## 3. Personal Data Inventory

CoreMCP가 처리하는 개인정보 카테고리:

| 카테고리 | 필드 | 저장 위치 | retention |
|---|---|---|---|
| 식별자 | email, name, avatar_url | users 테이블 | 계정 활성 + deletion grace 30d |
| 인증 | OAuth tokens, refresh tokens | external_connections + Redis | active session 기간 |
| 행위 로그 | tool_invocations metadata | tool_invocations | Free 90d / Paid 365d |
| 감사 로그 | audit_logs | audit_logs | 1y minimum |
| 네트워크 | IP, User-Agent | connection_tokens, audit_logs | 1y |
| 결제 | last4, brand | Stripe (CoreMCP는 token 참조만) | Stripe retention 정책 |
| 콘텐츠 | tool arguments/output | 기본 미저장 (ADR-009) | N/A |

## 4. Data Subject Rights

GDPR/개인정보보호법 동시 대응 endpoint 설계:

### 4.1 Right to Access (GDPR Art. 15, 개인정보보호법 §35)
- `POST /v1/me/export` — 사용자 본인 모든 데이터 export (JSON, NDJSON)
- 30일 이내 응답 보장 (자동화로 24h 이내 목표)

### 4.2 Right to Rectification (Art. 16, §36)
- `PATCH /v1/me` — profile 수정

### 4.3 Right to Erasure / 삭제 (Art. 17, §36)
- `DELETE /v1/me` — 계정 삭제 요청
- 즉시 soft-delete, 30d grace period 후 hard-delete
- audit_logs는 법적 보존 의무로 1y 유지 (anonymize)

### 4.4 Right to Restriction (Art. 18)
- `POST /v1/me/restrict` — 처리 일시 중단 (계정 freeze)

### 4.5 Right to Portability (Art. 20)
- Right to Access와 동일 endpoint, machine-readable format

### 4.6 Right to Object (Art. 21)
- marketing email opt-out
- profiling 미사용 명시

### 4.7 자동화 의사결정 미사용 (Art. 22)
- CoreMCP는 사용자에 영향 미치는 자동 의사결정 없음

## 5. Consent Management
- 가입 시: ToS + Privacy Policy 명시적 동의
- Marketing email: 별도 opt-in
- Tool description 공개(public marketplace): 별도 opt-in
- 분석 cookie: cookie banner (EU IP일 때)

## 6. Data Residency

### 6.1 MVP Single Region (ADR-023)
- 권장: ap-northeast-2 (서울) 또는 us-east-1
- 모든 데이터(DB/Redis/KMS/Object storage)가 동일 region

### 6.2 Multi-Region (Phase 5+)
- workspace.region 컬럼으로 routing
- EU 사용자 → eu-central-1
- US 사용자 → us-east-1
- KR 사용자 → ap-northeast-2
- 동의 없는 cross-region replication 금지

### 6.3 Backup Region
- 동일 region의 다른 AZ 1차
- 다른 region cross-replica는 사용자 명시 동의 시에만

## 7. Encryption

| 데이터 | 전송 중 | 저장 시 | Key 관리 |
|---|---|---|---|
| HTTPS 트래픽 | TLS 1.3 | N/A | ACM/Let's Encrypt |
| DB 컬럼(credential) | N/A | AES-256-GCM | AWS KMS envelope |
| DB at rest | N/A | RDS encryption | AWS KMS |
| S3 객체 | TLS 1.3 | SSE-KMS | AWS KMS |
| Redis | TLS 1.3 | optional encryption-at-rest | ElastiCache encrypted |
| Backups | TLS 1.3 | SSE-KMS | 동일 KMS |
| 로그(SIEM) | TLS 1.3 | provider 정책 | provider |

## 8. Subprocessor List
공개 페이지(https://coremcp.example.com/subprocessors)에 다음 명시:

| Subprocessor | 용도 | Region | DPA |
|---|---|---|---|
| AWS (RDS, S3, KMS, EC2) | infrastructure | ap-northeast-2 | yes |
| Logto Cloud (또는 self-host) | OAuth AS | 동일 region | yes |
| Stripe | 결제 처리 | global | yes |
| Sentry | 에러 추적 | EU instance 검토 | yes |
| OpenTelemetry 백엔드 | 관측성 | 동일 region | yes |
| Postmark / Resend | transactional email | EU | yes |

신규 subprocessor 추가 시 30일 사전 공지(Enterprise customer 한정).

## 9. Incident Response
- 데이터 침해 발견 → 24시간 내 internal escalation
- GDPR Art. 33: 72시간 내 supervisory authority 신고
- KR 개인정보보호법 §34: 24시간 내 통보
- 영향받은 사용자에게 60d 이내 통지
- postmortem 공개 (anonymized) — status page

## 10. Cookie / Tracking
- 필수 cookie: session, CSRF token (consent 불필요)
- analytics cookie: opt-in only (PostHog self-host 또는 Plausible)
- 광고/marketing pixel 없음

## 11. Children's Privacy
- 14세 미만(KR) / 13세 미만(US-COPPA) 가입 차단
- 가입 시 생년월일 또는 만 14세 이상 확인 (방식 미정)

## 12. Audit & Certification Roadmap
- T+0: 본 문서 공개
- T+3개월: Privacy Policy / ToS / DPA 공개
- T+6개월: SOC2 Type I 감사 시작
- T+12개월: SOC2 Type I 보고서 발급
- T+18개월: SOC2 Type II
- T+24개월: ISO 27001 검토

## 13. Data Processing Agreement (DPA)
B2B customer(Team/Enterprise)와 체결:
- subprocessor list
- security measures (Annex II)
- incident notification SLA
- audit rights
- data return / deletion on termination
- standard contractual clauses (SCC) for cross-border transfer

## 14. Right to be Forgotten 구현 디테일
- soft-delete: users.deleted_at, all owned resources cascaded soft-delete
- 30d grace: 사용자가 복구 요청 가능
- hard-delete after 30d:
  - users row 제거
  - mcp_services, toolboxes, external_connections 등 owner_user_id 기반 cascade
  - audit_logs는 actor_user_id를 NULL로 anonymize (법적 보존)
  - tool_invocations 동일 anonymize
  - secret_ref가 가리키는 KMS ciphertext destroy
  - backup region에서도 next backup cycle에 자연 삭제

## 15. Open Questions
1. EU 사용자 cutoff 시점은 언제?
2. SOC2 감사 기관 선정 (Coalfire / A-LIGN / Drata-managed)?
3. Stripe Tax로 KR 부가세 자동 처리 가능?
4. self-host Logto vs Logto Cloud(EU)? Cloud의 경우 EU SCC 필요
5. HIPAA BAA 필요한 healthcare MCP 등록 정책?
