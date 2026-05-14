# CoreMCP Risk Notes (Personal)

문서 버전: v1.0
작성일: 2026-05-11

본 문서는 개인 Mac mini 환경에서 실제로 발생 가능한 risk만 추린다. SaaS급 ATO 방어, refresh token theft, compliance violation 등은 `production_docs_donotuse/11-risk-review.md` 참조.

> 2026-05-14 동기화 메모: 본 문서의 Acceptance/Pre-Beta 체크리스트는 risk 운영 점검표다. 미체크 항목은 자동으로 “코드 미구현”을 뜻하지 않으며, 실제 reboot/Tailscale/mobile/long soak처럼 운영 환경에서 반복 확인해야 하는 항목을 포함한다. 현재 실행 완료 snapshot과 remaining work 분류는 [`../TESTING.md`](../TESTING.md)를 우선한다.

---

## 1. 적용 가능한 Risk Matrix

| ID | Risk | Severity | Likelihood | Priority |
|---|---|---|---|---|
| R-101 | Token passthrough (CoreMCP token이 downstream에) | High | Low | P0 |
| R-102 | Credential 평문 누설 (DB/log/UI) | High | Low | P0 |
| R-103 | SSRF via MCP endpoint URL | Medium | Medium | P0 |
| R-104 | Tool poisoning (downstream description 악성) | Medium | Medium | P1 |
| R-105 | Schema drift 미감지 | Low | Medium | P1 |
| R-106 | Keychain unlock 실패 (재부팅 직후) | Medium | High | P1 |
| R-107 | Personal token 노출 (sessionStorage XSS or git commit) | High | Low | P1 |
| R-108 | SQLite 락 / 손상 | Low | Low | P2 |
| R-109 | Mac mini 하드웨어 장애 | High | Low | P1 |
| R-110 | launchd 시작 실패 | Medium | Low | P2 |
| R-111 | Tailscale 노출 후 외부 brute force | Medium | Low | P2 |
| R-112 | Downstream MCP supply chain (schema 악의적 변경) | Medium | Low | P2 |
| R-113 | Cost explosion (downstream API 무한 호출) | Medium | Low | P2 |
| R-114 | Mac mini 디스크 풀 (logs/SQLite 비대) | Medium | Medium | P1 |
| R-115 | Dual token drift (admin/client 불일치) | Low | Low | P2 |

---

## 2. R-101 Token Passthrough

### Description
CoreMCP가 받은 bearer를 downstream Authorization 헤더에 그대로 전달하면 audience boundary 깨짐.

### Impact
- downstream에서 잘못된 신뢰 부여
- downstream 측 로그에 본인 personal token 노출

### Mitigation
- CoreMCP token type과 downstream credential type 코드 레벨 분리
- DownstreamMcpClient는 service_credentials에서만 Authorization 헤더 생성
- integration test에서 fake-mcp header recording

### Acceptance
- 모든 downstream call test에서 CoreMCP token 미전송

---

## 3. R-102 Credential 평문 누설

### Description
API key / bearer / OAuth token이 DB / logs / UI에 평문 노출.

### Mitigation
- vault(Keychain or fernet)에만 평문 저장
- DB는 secret_ref만
- UI는 masked_value만
- logger redaction: authorization/token/api_key 등 키 자동 마스킹
- audit/invocation log는 metadata만

### Acceptance
- `sqlite3 db.sqlite3 .dump | grep -E 'ghp_|sk_|Bearer '` → 결과 없음
- `cat ~/.coremcp/logs/coremcp.log | grep -E 'Bearer [A-Za-z0-9]{20,}'` → 결과 없음

---

## 4. R-103 SSRF via MCP Endpoint URL

### Description
본인이 실수로 또는 악성 redirect로 내부망 / cloud metadata endpoint를 호출.

### Mitigation
- HTTPS 강제 (단 localhost http 예외)
- 169.254.169.254, private IP block
- DNS resolve 매 outbound call 직전 재검사
- max redirect = 0
- 06-security-auth.md §7 참조
- ALLOW_PRIVATE_DOWNSTREAM / ALLOW_TAILSCALE_DOWNSTREAM / ALLOWED_PRIVATE_CIDRS allowlist 모델 (ADR-033)

### Acceptance
- SSRF unit test 케이스 (§3.1 in 10-test-plan.md) 모두 통과
- 169.254.169.254 등록 실패

---

## 5. R-104 Tool Poisoning

### Description
등록한 downstream MCP가 후속 schema 업데이트로 악성 description 주입.

### Mitigation
- ToolMetadataScanner: regex pattern + Unicode/homoglyph
- description max 1024 chars
- risk_level high tool은 UI에 경고 + tools/call 시 confirm 옵션
- schema_hash 변경 시 listChanged 후 사용자 확인

### Acceptance
- "ignore previous instructions" pattern 100개 sample 100% 검출
- false positive rate 측정

---

## 6. R-105 Schema Drift 미감지

### Description
downstream tool schema가 변경됐는데 캐시가 오래되어 tools/call이 실패하거나 잘못된 인자.

### Mitigation
- schema_hash로 변경 감지
- TTL 1h (private) + listChanged 즉시 invalidate
- tools/call schema error → lazy refresh
- manual refresh-tools 버튼

### Acceptance
- E2E-004 통과
- refresh 후 cache 즉시 반영

---

## 7. R-106 Keychain Unlock 실패

### Description
Mac mini 재부팅 후 login.keychain이 잠긴 상태에서 launchd가 CoreMCP 시작 → keyring.get_password 실패 → credential resolve 불가.

### Mitigation 옵션
1. **자동 로그인** 활성 (가장 간단, login.keychain 자동 잠금 해제)
2. SecurityAgent로 keychain 잠금 해제 보장
3. fallback: fernet backend로 전환 (master key는 file)
4. CoreMCP가 시작 시 keychain 접근 테스트, 실패 시 service status=auth_required로 표시

### Acceptance
- 재부팅 후 5분 이내 credential resolve 성공
- 실패 시 명확한 에러 로그 + UI 상태

---

## 8. R-107 Token 노출 (Admin / Client)

### Description
- admin token (`cmcp_admin_*`)이 sessionStorage XSS, git 커밋, 화면 공유로 노출
- client token (`cmcp_client_*`)이 동일하게 노출
- 또는 client token 발급 modal에서 사용자가 안전히 보관 안 함

### Impact
- admin token 노출: /v1/* root 접근 → 모든 service / credential / log 접근
- client token 노출: 해당 client connection의 tool 호출 가능 (revoke 전까지)

### Mitigation
- admin: ~/.coremcp/admin-token chmod 600, .gitignore에 ~/.coremcp/
- client: DB hash만 저장, 발급 응답 평문 1회 노출 → Web UI에 명확한 경고
- sessionStorage에는 admin token만, nonce CSP `script-src 'self' 'nonce-...'`
- Tailscale 외부 노출 시 HTTPS 강제
- 의심 시 admin token 회전, 모든 client token 일괄 revoke
- audit log: admin_token.rotate, client_token.issue, client_token.revoke

### Acceptance
- Web UI XSS 테스트 시 admin token 탈취 불가
- git diff에 token 평문 노출 X
- DB grep으로 client token 평문 없음

---

## 9. R-108 SQLite 락 / 손상

### Description
WAL 모드 미적용 또는 멀티 프로세스 동시 write로 락 발생. 또는 파일 시스템 오류로 손상.

### Mitigation
- `PRAGMA journal_mode=WAL`
- `PRAGMA foreign_keys=ON`
- 동시 write는 BackgroundTasks 단일 worker로 직렬화
- daily backup (sqlite3 .backup)
- 손상 시 backup 복구 절차 문서화 (12-operations.md §8)

### Acceptance
- 락 발생률 < 0.1%
- 복구 dry-run 성공

---

## 10. R-109 Mac mini 하드웨어 장애

### Description
디스크 / 메모리 / 전원 장애로 데이터 손실.

### Mitigation
- Time Machine 외장 디스크 또는 iCloud Drive로 `~/.coremcp/` 동기화
- daily SQLite .backup 별도 위치
- credential은 Keychain 동기화(iCloud Keychain) 옵션
- 하드웨어 교체 시 backup 복원 절차

### Acceptance
- RPO: 24시간
- RTO: 2시간 (새 Mac 또는 복원)

---

## 11. R-110 launchd 시작 실패

### Description
plist 오류 / 권한 / 경로 / Python 환경으로 daemon 시작 실패.

### Mitigation
- plist에 절대 경로
- working directory 명시
- stdout/stderr 파일 redirect (~/.coremcp/logs/launchd.{stdout,stderr})
- 첫 설치 후 reboot 테스트
- launchctl bootstrap 명령 README

### Acceptance
- 재부팅 시 5분 내 health=ok

---

## 12. R-111 Tailscale 외부 노출 후 Brute Force

### Description
Tailscale 안에서도 token brute force / 다른 노드 침해 시.

### Mitigation
- Tailscale ACL: 본인 노드만
- HTTPS 강제 (Tailscale Serve 또는 Caddy)
- token 길이 256-bit
- 401 응답에 빠른 응답 + 로깅 (이상 패턴 감지)
- 옵션: Tailscale Access에서 추가 인증

### Acceptance
- ACL 점검 후 본인 디바이스 외 접근 불가

---

## 13. R-112 Downstream Supply Chain

### Description
신뢰한 downstream MCP 운영자가 악성 update 배포.

### Mitigation
- schema_hash 변경 시 사용자 알림 (in-app)
- public service는 미사용 (private만)
- annotations destructive=true tool은 별도 confirm
- 자체 만든 MCP 위주로 사용 권장

### Acceptance
- schema 변경 알림 동작 (E2E-004)

---

## 14. R-113 Cost Explosion

### Description
LLM이 loop tool_call로 downstream API 비용 폭발 (특히 paid GitHub/OpenAI API).

### Mitigation
- per-process global rate limit (tools/call 300/min)
- per-service rate limit (300/min)
- 동시 5개 in-flight cap
- 에러율 30% 초과 시 1분 backoff
- 본인이 dashboard에서 invocation 수 모니터링

### Acceptance
- 비정상 호출 1분 내 차단

---

## 15. R-114 디스크 풀

### Description
logs / tool_invocations / debug_traces가 누적되어 디스크 풀.

### Mitigation
- log rotation (daily, 7일 보관, gzip)
- tool_invocations 90일 retention (cleanup job)
- debug_traces 24h auto-delete
- audit_logs 1년 retention
- monthly check: `du -sh ~/.coremcp/`
- alert: disk usage > 80% (옵션 macOS notification)

### Acceptance
- 6개월 운영 후 ~/.coremcp/ < 10GB

---

## 16. R-115 Dual Token Drift

### Description
admin token 회전 + client token 일괄 revoke 누락 시 보안 hole. 또는 external_connection 삭제했지만 token CASCADE 실패.

### Mitigation
- external_connections DELETE 시 personal_access_tokens ON DELETE CASCADE (05 §9.3)
- DB CHECK constraint: revoked_at IS NOT NULL ⇔ status='revoked'
- Web UI Settings/Tokens에 "최근 30일 inactive client revoke" 일괄 작업
- audit log로 두 토큰 종류 movement 추적

### Acceptance
- external_connections DELETE 후 관련 client token이 즉시 401
- audit query: 활성 client token이 revoked external_connection을 참조하지 않음

---

## 17. Pre-Beta Checklist (개인 컨텍스트)

아래 목록은 구현 gap 리스트가 아니라 release 전 risk 확인표다. 외부환경 검증, 운영 반복 점검, 보안 수동 확인이 섞여 있으므로 stale unchecked box만 보고 backend backlog로 해석하지 않는다.

- [ ] R-101 token passthrough test pass
- [ ] R-102 credential encryption 확인
- [ ] R-103 SSRF guard 작동
- [ ] R-104 metadata scanner 활성
- [ ] R-105 schema drift 알림
- [ ] R-106 keychain unlock 시나리오 명시
- [ ] R-107 token 파일 chmod 600 + .gitignore
- [ ] R-108 SQLite WAL 모드 활성
- [ ] R-109 Time Machine 또는 backup script 설정
- [x] R-110 launchd plist 검증 — api/web/backup/logrotate/refresh `plutil` OK, actual reboot은 외부환경 검증
- [ ] R-111 Tailscale ACL 점검 (해당 시) — Tailscale CLI 설치/로그인 후 외부환경 검증
- [ ] R-112 schema drift 알림 동작
- [ ] R-113 rate limit 동작
- [x] R-114 log rotation 설정 — `rotate-logs.sh` + `com.coremcp.logrotate.plist`; launchd load smoke 통과, reboot 지속성은 외부환경 검증
- [ ] R-115 admin/client token 일관성 확인 (CASCADE 동작)
- [ ] ~/.coremcp/admin-token chmod 600
- [ ] Web UI에 client token 발급 modal 안전 경고 표시

---

## 18. 개인 컨텍스트라 제외하는 Risk

production_docs_donotuse/11-risk-review.md의 다음 risk는 본 프로젝트에 적용 안 됨:
- R-004 User isolation failure (단일 사용자)
- R-008 Client compatibility (Claude Code 우선이라 단순)
- R-009 Marketplace abuse (marketplace 없음)
- R-013 Refresh token theft (refresh 미사용)
- R-014 OAuth open redirect (OAuth 옵션)
- R-017 Data residency (단일 호스트, GDPR 무관)
- R-018 DDoS on OAuth callback (외부 노출 제한)
- R-020 Marketplace brand impersonation (marketplace 없음)
- R-021 KMS vendor outage (Keychain 사용)
- R-022 DB migration breaking change (소규모, 본인이 검증)
- R-024 Dependency CVE (개인 dev, 주기 점검)
