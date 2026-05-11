# CoreMCP Future SaaS Migration (Personal → SaaS)

문서 버전: v1.0
작성일: 2026-05-11

본 문서는 개인 프로젝트로 시작한 CoreMCP를 다인 사용 SaaS로 확장하기로 결정할 경우의 절차를 정의한다. 현재는 trigger 미발생 상태이며, 결정 시 본 문서 + `production_docs_donotuse/`를 reference로 작업한다.

---

## 1. 전환 Trigger 후보

다음 중 하나라도 발생 시 SaaS 전환을 진지하게 검토:
- 본인 외 신뢰하는 사용자 1명 추가 (가족, 팀원)
- 회사 동료 5명 이상이 사용하고 싶어함
- 공개 marketplace로 매출 가능성
- 외부 OpenSource로 공개

전환 안 하는 경우의 합리적 path:
- 본인용으로만 영구 유지
- 가까운 사람에게는 별도 Mac mini deploy 권장

---

## 2. 영향 받는 ADR

| 현재 ADR | 상태 변화 | 새 ADR 작성 필요 |
|---|---|---|
| ADR-020 Data Region = Local | Superseded | 새 ADR: Multi-region 또는 single cloud region |
| ADR-021 Pricing = None | Superseded | 새 ADR: Freemium / 4-tier |
| ADR-022 License = Private | Superseded | 새 ADR: Closed SaaS + OSS SDK 등 |
| ADR-027 Right-to-Erasure Manual | Superseded | 새 ADR: GDPR compliant API |
| ADR-011 Static Bearer | Superseded | 새 ADR: OAuth 2.1 + Logto |
| ADR-012 Keychain Vault | Superseded | 새 ADR: AWS KMS envelope |
| ADR-018 BackgroundTasks Worker | Superseded | 새 ADR: Arq 또는 Celery |
| ADR-019 SQLite DB | Superseded | 새 ADR: PostgreSQL with RLS |
| ADR-028 Single Process | Superseded | 새 ADR: Multi-process Kubernetes |
| ADR-036 CIMD First, DCR Fallback | Active in SaaS | 현재는 latent (AUTH_MODE=oauth 비활성), SaaS 전환 시 활성 |

production_docs_donotuse/13-adr.md의 ADR-011, 012, 017, 022, 023, 024, 025 적용.

---

## 3. 기술 마이그레이션 체크리스트

### 3.1 인증
- [ ] Logto self-host 또는 Cloud 결정
- [ ] DCR endpoint 활성
- [ ] PKCE S256 mandatory
- [ ] Resource Indicator (RFC 8707) strict
- [ ] JWKS endpoint + rotation
- [ ] Refresh token rotation + family detection
- [ ] OAuth consent screen
- [ ] static bearer → JWT RS256 마이그레이션
- [ ] 기존 user의 token을 user_id에 binding
- [ ] CIMD endpoint handler 활성
- [ ] CIMD metadata fetch SSRF guard 적용 (06-security-auth §7.5)
- [ ] CIMD response cache (TTL 1h)
- [ ] CIMD vs DCR vs pre-registered client lookup 우선순위 구현
- [ ] CIMD rate limit 30/hour/IP
- [ ] Origin validation 강화 (403 + audit, 06-security-auth §12.4)
- [ ] AUTH_MODE 전환 시 무중단 정책
- [ ] admin/client token 모델 → 모든 사용자에게 동일 모델 유지 + JWT(SaaS) 추가

### 3.2 DB
- [ ] SQLite → PostgreSQL 15+ 마이그레이션 스크립트
- [ ] UUID 타입 변환 (TEXT → UUID)
- [ ] TIMESTAMP → TIMESTAMPTZ
- [ ] JSON → JSONB
- [ ] INET 적용
- [ ] CHECK → ENUM
- [ ] RLS 정책 활성 (모든 owner_user_id 테이블)
- [ ] tool_invocations / audit_logs monthly partition
- [ ] pg_partman 자동화
- [ ] tool_aliases.exposed_name UNIQUE 제약 변경: global unique → (toolbox_id, exposed_name) 또는 (user_id, exposed_name) scope
  - 이유: 개인용에서는 사용자 1명이라 global unique OK. SaaS에서는 user A의 `github.create_issue`와 user B의 `github.create_issue`가 충돌
  - 마이그레이션:
    ```sql
    DROP INDEX uq_tool_aliases_exposed_name_active;
    -- tool_aliases에 owner_user_id (또는 toolbox_id) 컬럼 추가 backfill
    CREATE UNIQUE INDEX uq_tool_aliases_exposed_name_per_owner_active
      ON tool_aliases(owner_user_id, exposed_name)
      WHERE deprecated_at IS NULL;
    ```
  - alternative: (toolbox_id, exposed_name)으로 scope, toolbox 간 동일 이름 허용
  - 권장: per-user (단일 toolbox 가정 SaaS 초기), 후속 multi-toolbox에서 per-toolbox로 추가 좁힘

### 3.3 Multi-tenant
- [ ] workspaces / workspace_members 테이블 활성
- [ ] workspace_id NOT NULL 마이그레이션 (NULL → personal workspace)
- [ ] RBAC role enum
- [ ] workspace invitation flow
- [ ] workspace 단위 quota
- [ ] 모든 owner_user_id 또는 workspace_id 컬럼이 partial unique index의 scope에 포함되었는지 검토
- [ ] 특히 다음 테이블의 UNIQUE 제약이 user/workspace scope로 변경되었는지:
  - mcp_services (현재 owner_user_id, slug) → 이미 scope됨, OK
  - toolboxes (slug는 nullable, scope 미적용 OK)
  - tool_aliases (global → user/workspace scope, 위 §3.2 참조)
  - external_connections (현재 user 단위, OK)
- [ ] 모든 partial unique index가 (ADR-035 정책 준수) workspace_id 추가 scope로 갱신 — SaaS 전환 시 user/workspace 단위 분리

### 3.4 Credential Vault
- [ ] AWS KMS envelope encryption 도입
- [ ] DEK rotation 정책
- [ ] KEK yearly rotation
- [ ] Keychain → KMS 마이그레이션 스크립트 (decrypt → re-encrypt)
- [ ] cross-region replica

### 3.5 Cache / Worker
- [ ] In-memory cache → Redis cluster
- [ ] cache key 표준 `cache:catalog:user:{user_id}`
- [ ] Redis pub/sub invalidation
- [ ] BackgroundTasks → Arq worker pool

### 3.6 Compliance
- [ ] GDPR / 개인정보보호법 대응 (16-compliance.md 활성)
- [ ] right-to-erasure API + 30d grace
- [ ] data export API + S3 signed URL
- [ ] Privacy Policy / ToS / DPA / Subprocessor list 공개
- [ ] cookie banner
- [ ] consent management
- [ ] OAuth client metadata (CIMD) public publication 정책 (subprocessor list와 분리)
- [ ] 외부 OAuth client의 brand impersonation 검토

### 3.7 Operations
- [ ] Mac mini → cloud 호스팅 (AWS Fargate, Fly.io 등)
- [ ] Multi-region 검토 (단일 region MVP)
- [ ] DR / backup region
- [ ] PagerDuty / on-call
- [ ] status page
- [ ] incident response SEV1~4

### 3.8 Billing
- [ ] Stripe 통합
- [ ] subscription / plan tier
- [ ] usage metering
- [ ] invoicing
- [ ] Stripe Tax (KR VAT)

### 3.9 Pricing
- [ ] 가격 결정 (production_docs_donotuse/14-pricing.md 활성)
- [ ] Free → Pro → Team → Enterprise
- [ ] quota 매트릭스
- [ ] fair use policy

### 3.10 Marketplace (Phase 4+)
- [ ] public registry
- [ ] review queue
- [ ] verified badge
- [ ] abuse report
- [ ] revenue share (Stripe Connect)

### 3.11 Client Compatibility
- [ ] DCR multi-client 검증 (Claude, ChatGPT, Cursor)
- [ ] 17-mcp-client-profiles 활성

### 3.12 Frontend
- [ ] sign-up / login / email verify
- [ ] MFA enroll
- [ ] password reset
- [ ] pricing page (public)
- [ ] billing portal
- [ ] workspace switcher
- [ ] member invitation
- [ ] marketplace browse
- [ ] cookie banner
- [ ] legal pages (ToS / Privacy / DPA download)

### 3.13 Security
- [ ] per-user rate limit (현재 global → per user)
- [ ] account takeover defense
- [ ] anomaly detection
- [ ] bug bounty 정책
- [ ] SOC2 Type I → Type II audit
- [ ] penetration test

---

## 4. Migration Strategy

### 4.1 Big-bang vs Incremental
권장: **Incremental**. SaaS 인스턴스를 신규 region에 새로 띄우고, 본인의 Mac mini는 그대로 운영. 신규 사용자는 SaaS로, 본인은 양쪽 사용 가능 후 점진 전환.

### 4.2 데이터 이전
본인의 Mac mini SQLite → SaaS PostgreSQL은 1회성 export/import:
1. SaaS user 생성 (이메일/OAuth)
2. mcp_services, service_tools, tool_aliases, toolboxes, toolbox_items, external_connections export (NDJSON)
3. credentials는 Keychain → 신규 KMS로 재암호화 (사용자가 secret 재입력 권장)
4. 새 access token 발급
5. Claude Code 재등록
- credential 재암호화: macOS Keychain / fernet → AWS KMS envelope (단발 마이그레이션 스크립트)
- icons CDN 마이그레이션: ~/.coremcp/icons → S3 + CloudFront
- 데이터 이전 중 admin token은 기존 사용자 1명에게 유지, 그 외 신규 user는 OAuth + per-client token
- personal_access_tokens은 SaaS에서도 동일 모델로 유지 (단 workspace_id 컬럼 추가)

### 4.3 Data Residency
SaaS 시점에 region 결정:
- 한국 사용자 우선: ap-northeast-2
- 미국 우선: us-east-1
- EU: eu-central-1 (GDPR)

본인 데이터는 위 region 중 1개에 위치.

---

## 5. Cost Estimate (SaaS 전환 시)

월 비용 추정 (1000 active user 기준):
- AWS RDS Postgres small: ~$50
- AWS ElastiCache Redis small: ~$25
- AWS KMS API: ~$5
- S3: ~$5
- CloudFront: ~$10
- Logto Cloud: ~$30 (또는 EC2 self-host $20)
- App hosting (Fargate / Fly): ~$50
- Sentry / Datadog: ~$30
- 총 약 $200-300/월

freemium 구조에서 break-even은 paid user 약 15-20명.

---

## 6. 전환 안 하는 경우의 hardening

본인 사용에 머무를 때 향상 가능 항목:
- 자체 만든 MCP 풀 확장 (5+ services)
- launchd → 더 견고한 process manager (PM2, supervisor, runit)
- Time Machine 외부 추가 백업 (Backblaze, rsync to NAS)
- 옵션 OAuth 활성 (ChatGPT/Cursor 사용 시작 시)
- multi-toolbox 활성 (목적별: work/personal/experimental)
- 옵션 AUTH_MODE=oauth 활성 + CIMD 처리 (ChatGPT Apps 사용 시작)
- protocol version 협상 정책 자동 회귀 테스트
- 개인용 영구 운영 시 tool_aliases global unique 제약은 그대로 OK
- 단 multi-user 시도 시 (예: 가족 사용) 위 §3.2 마이그레이션 선행 필요

---

## 6.1 추가로 필요한 문서 후보

- 21-oauth-cimd-policy.md (CIMD metadata 검증 정책)
- 22-icons-cdn-policy.md (icons 외부 hosting / CDN)

---

## 7. 결정 timeline

| 단계 | 일정 |
|---|---|
| 개인용 MVP 운영 | 현재 |
| Phase P3 완료 | +1개월 |
| 1년 본인 사용 + 안정성 검증 | +12개월 |
| SaaS 전환 검토 (trigger 발생 시) | trigger 시점 |
| Migration 작업 (전환 시) | +3개월 |
| 공개 베타 | +6개월 |

본인 사용에 영원히 머무를 수도 있다. SaaS 전환은 사용자 요구가 명확할 때만.

---

## 8. 참고
- `production_docs_donotuse/`: SaaS 청사진 17개 문서 모두 reference
- `production_docs_donotuse/13-adr.md`: 원본 25개 ADR
- `production_docs_donotuse/16-compliance.md`: GDPR/SOC2 로드맵
- `production_docs_donotuse/14-pricing.md`: 가격 정책 후보
- `production_docs_donotuse/_v1-personal-implementation.md`: 본 프로젝트 이전 초안 (역사적 reference)
