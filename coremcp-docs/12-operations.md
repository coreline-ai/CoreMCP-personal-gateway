# CoreMCP Operations (Personal)

문서 버전: v1.0
작성일: 2026-05-11

> 2026-05-14 동기화 메모: 이 문서는 운영 runbook이다. 체크리스트의 미체크 항목은 구현 backlog가 아니라 daily/weekly/monthly recurring task 또는 실제 Mac mini/Tailscale/mobile/long-soak 환경에서 확인할 operational validation일 수 있다. 실행된 테스트 snapshot은 [`../TESTING.md`](../TESTING.md)를 우선한다.

---

## 1. 운영 목표

Mac mini에서 무인 운영하면서:
- 재부팅 후 자동 복귀
- 일일 백업
- 로그/메트릭 사용
- 장애 시 1시간 이내 복구
- 본인이 즉시 상태 파악

---

## 2. Daemon (launchd)

### 2.1 API daemon
`~/Library/LaunchAgents/com.coremcp.api.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.coremcp.api</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/me/.local/bin/uv</string>
    <string>run</string>
    <string>--directory</string>
    <string>/Users/me/projects/coremcp/apps/api</string>
    <string>uvicorn</string>
    <string>coremcp.main:app</string>
    <string>--host</string><string>127.0.0.1</string>
    <string>--port</string><string>8787</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/Users/me/projects/coremcp/apps/api</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>COREMCP_DATA_DIR</key><string>/Users/me/.coremcp</string>
    <key>DATABASE_URL</key><string>sqlite+aiosqlite:////Users/me/.coremcp/data/db.sqlite3</string>
    <key>COREMCP_ADMIN_TOKEN_FILE</key><string>/Users/me/.coremcp/admin-token</string>
    <key>COREMCP_CORS_ALLOWED_ORIGINS</key><string>http://localhost:3003,http://127.0.0.1:3003</string>
    <key>AUTH_MODE</key><string>static_bearer</string>
    <key>SECRET_BACKEND</key><string>keychain</string>
    <key>ALLOW_LOOPBACK_DOWNSTREAM</key><string>true</string>
    <key>MCP_SUPPORTED_VERSIONS</key><string>2025-11-25,2025-06-18</string>
  </dict>
  <key>StandardOutPath</key>
  <string>/Users/me/.coremcp/logs/launchd.api.stdout</string>
  <key>StandardErrorPath</key>
  <string>/Users/me/.coremcp/logs/launchd.api.stderr</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key><false/>
    <key>Crashed</key><true/>
  </dict>
  <key>ThrottleInterval</key><integer>10</integer>
</dict>
</plist>
```

설치:
```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.coremcp.api.plist
launchctl enable gui/$(id -u)/com.coremcp.api
launchctl kickstart gui/$(id -u)/com.coremcp.api
```

상태 확인:
```bash
launchctl print gui/$(id -u)/com.coremcp.api | head -40
launchctl print-disabled gui/$(id -u) | grep coremcp
```

언로드:
```bash
launchctl bootout gui/$(id -u)/com.coremcp.api
```

### 2.2 Web daemon (옵션)
별도 plist `com.coremcp.web` — Next.js build 후 `next start --port 3003` 또는 정적 export.

현재 repo에는 실제 검증용 helper가 포함되어 있다.

```bash
mkdir -p ~/.coremcp/{data,logs,backups}
chmod 700 ~/.coremcp

# root에서 실행
pnpm build
infra/scripts/coremcp-launchctl.sh load
infra/scripts/ops-smoke.sh
make ui-smoke

# 재부팅 후 수동 검증
infra/scripts/ops-smoke.sh --post-reboot

# 종료
infra/scripts/coremcp-launchctl.sh unload
```

2026-05-13 로컬 검증 결과:
- `com.coremcp.fake-mcp`: launchd load 후 `http://127.0.0.1:8790/health` 200
- `com.coremcp.api`: launchd load 후 `http://127.0.0.1:8787/ready` 200
- `com.coremcp.web`: launchd load 후 `http://127.0.0.1:3003/` 200
- `com.coremcp.backup`: daily 03:00 schedule label load 확인
- `com.coremcp.logrotate`: daily 00:15 plist 추가, `plutil`과 `ops-smoke` label logic 통과
- `com.coremcp.refresh`: daily 03:20 active service validation schedule, `plutil`과 no-service runner smoke 통과
- Reboot 검증은 실제 macOS 재부팅이 필요하므로 `--post-reboot` runbook으로 분리

### 2.3 Worker daemon (옵션)
Arq 사용 시 `com.coremcp.worker` 추가. MVP는 API 프로세스 내 BackgroundTasks.

---

## 3. Logs

### 3.1 위치
- API stdout/stderr: `~/.coremcp/logs/api.log`, `~/.coremcp/logs/api.err.log`
- structured JSON: `~/.coremcp/logs/coremcp.log` (structlog)
- web Next.js: `~/.coremcp/logs/web.log`, `~/.coremcp/logs/web.err.log` (옵션)
- audit / invocation은 DB

### 3.2 형식 (structlog JSON)
```json
{
  "timestamp": "2026-05-11T12:34:56Z",
  "level": "info",
  "logger": "coremcp.gateway",
  "request_id": "req_...",
  "user_id": "usr_local",
  "external_connection_id": "ext_...",
  "method": "tools/call",
  "exposed_tool_name": "github.create_issue",
  "status": "success",
  "latency_ms": 820
}
```

### 3.3 보기
```bash
tail -f ~/.coremcp/logs/coremcp.log | jq -r 'select(.level=="error")'
tail -f ~/.coremcp/logs/coremcp.log | jq 'select(.method=="tools/call")'
```

### 3.4 Redaction Keys
```text
authorization, cookie, set-cookie,
api_key, apikey, token, refresh_token, access_token,
password, secret, client_secret, x-api-key, credential, private_key,
cmcp_admin_, cmcp_client_, admin_token, client_token
```

### 3.5 Rotation

Repo에는 log rotation 전용 스크립트와 launchd plist가 있다.

```bash
# 수동 실행
infra/scripts/rotate-logs.sh

# launchd daily 00:15 logrotate + daily 03:20 refresh labels
plutil -lint infra/launchd/com.coremcp.logrotate.plist infra/launchd/com.coremcp.refresh.plist
infra/scripts/coremcp-launchctl.sh load
launchctl list | grep -E "com.coremcp.(logrotate|refresh)"
```

기본 정책:
- `COREMCP_LOG_DIR` 기본값: `~/.coremcp/logs`
- `*.log` 파일이 10MB를 넘으면 gzip 압축
- `*.log`와 `*.log.gz` 모두 `COREMCP_LOG_RETENTION_DAYS`(기본 7일) 초과분 삭제

2026-05-14 상태: plist syntax와 `ops-smoke.sh`의 `fake-mcp/api/web/backup/logrotate/refresh` label logic/load smoke는 통과. 실제 reboot 검증은 운영 host 재부팅 후 `ops-smoke.sh --post-reboot`로 확인한다.

---

## 4. Health / Metrics

### 4.1 Health Endpoints
```bash
curl http://localhost:8787/health
curl http://localhost:8787/ready
curl http://localhost:8787/live
```

응답:
```json
{ "status": "ok" }
```

`/ready`는 DB / Vault / (옵션) Redis 점검.

### 4.2 Metrics (옵션)
`METRICS_ENABLED=true` 환경 변수 → `/metrics` Prometheus format.
Mac mini에 grafana + prometheus 띄울 시 활용.

2026-05-14 상태: API runtime은 proactive service health probe를 백그라운드로 실행한다.

```bash
COREMCP_SERVICE_HEALTH_PROBE_ENABLED=true
COREMCP_SERVICE_HEALTH_PROBE_INTERVAL_SECONDS=60
COREMCP_SERVICE_HEALTH_PROBE_TIMEOUT_SECONDS=2
```

본인용 주요 metric:
- `mcp_requests_total{method,status}`
- `mcp_request_duration_ms{method}`
- `downstream_requests_total{service,status}`
- `downstream_latency_ms{service}`
- `tool_invocations_total{service,status}`
- `coremcp_mcp_services_health_failing`
- `coremcp_mcp_services_circuit_open`
- `cache_hits_total{layer}`
- `cache_misses_total{layer}`
- `vault_resolve_latency_ms`

### 4.3 본인용 dashboard
- Web UI Dashboard 페이지가 1차
- 옵션: grafana panel 1개

---

## 5. Backup

### 5.1 SQLite backup
```bash
# 수동 1회 실행
infra/scripts/backup-sqlite.sh
```

launchd daily schedule:
```bash
plutil -lint infra/launchd/com.coremcp.backup.plist infra/launchd/com.coremcp.logrotate.plist infra/launchd/com.coremcp.refresh.plist
infra/scripts/coremcp-launchctl.sh load
launchctl list | grep -E "com.coremcp.(backup|logrotate|refresh)"
```

기본 정책: 매일 03:00, `~/.coremcp/backups/coremcp-*.sqlite3`, 7일 이상 파일 삭제.

### 5.2 Scheduled service refresh

```bash
# 수동 1회 실행
cd apps/api
uv run python -m coremcp.refresh

# refresh 대상 status 조정
COREMCP_REFRESH_STATUSES=active,error uv run python -m coremcp.refresh
```

기본 정책: 매일 03:20, `active` service만 대상으로 `validate_service()`를 재사용한다. API runtime과 동일한 vault, SSRF guard, timeout, downstream response sanitizer를 사용한다.

launchd log:
- stdout: `~/.coremcp/logs/refresh.log`
- stderr: `~/.coremcp/logs/refresh.err.log`

### 5.3 전체 디렉토리 backup
- Time Machine으로 `~/.coremcp/` 포함
- 또는 iCloud Drive 동기화 (~/.coremcp가 ~/Library/Mobile Documents 아래 심볼릭 링크 옵션)
- 또는 rsync to NAS

### 5.4 Credential backup
- macOS Keychain은 iCloud Keychain 활성 시 자동 동기화
- Fernet master key(`FERNET_KEY_FILE`, 기본 `~/.coremcp/data/secrets.key`)는 별도 안전한 곳에 보관 (1Password 등)

### 5.5 Backup 복원
```bash
launchctl bootout gui/$(id -u)/com.coremcp.api
infra/scripts/restore-sqlite.sh ~/.coremcp/backups/coremcp-20260511T030000Z.sqlite3
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.coremcp.api.plist
```

`restore-sqlite.sh` 는 복원 직전 현재 DB 를 `coremcp.sqlite3.pre-restore-<UTC>` 로 자동 보관하고, 백업 파일의 `PRAGMA integrity_check;` 통과만 복원한다.

RPO 목표: 24시간
RTO 목표: 2시간

## 5.6 DR (Disaster Recovery)

운영 중 가장 자주 묻는 3가지 시나리오를 한 페이지에서 답한다. 모든 명령은 운영 host (서비스 daemon이 도는 머신) 에서 실행한다.

### DR-1 Fernet 키 손실 대응

영향 범위: `FERNET_KEY_FILE` (기본 `~/.coremcp/data/secrets.key`) 이 사라지면 file vault 백엔드에 저장된 모든 encrypted credential 이 영구 복구 불가. macOS Keychain 백엔드는 Keychain 자체가 살아있으면 무관.

```bash
# 1) 평소 백업 (정기 권장): Fernet 키를 1Password / 안전한 외부 저장소로 export
cp ~/.coremcp/data/secrets.key ~/Documents/secrets.key.backup
# 2) 키 손실 발생 시 복구
cp ~/Documents/secrets.key.backup ~/.coremcp/data/secrets.key
chmod 600 ~/.coremcp/data/secrets.key
make stop && make run
# 3) 백업이 없는 경우: file vault 의 credential 은 회복 불가 → 모든 service credential 재발급 필요
make stop
rm ~/.coremcp/data/secrets.key
# 새 키가 자동 생성되며, credentials_secrets.json 의 기존 record 는 사용 불가
make run
# Web Admin > Services 에서 영향받은 service 의 credential 을 재입력
```

### DR-2 SQLite 손실 / 손상 복구

```bash
# 1) 최신 백업 찾기
ls -lh ~/.coremcp/backups/coremcp-*.sqlite3 | tail -3
# 2) launchd daemon 정지 (열린 file handle 해제)
launchctl bootout gui/$(id -u)/com.coremcp.api
# 3) restore (자동 integrity_check + pre-restore 백업)
infra/scripts/restore-sqlite.sh ~/.coremcp/backups/coremcp-<UTC>.sqlite3
# 4) 검증 → daemon 재기동
cd apps/api && uv run coremcp doctor --api-url http://127.0.0.1:8787
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.coremcp.api.plist
```

만약 백업 자체가 손상되어 integrity_check 통과 못 하면 더 이전 백업을 선택한다. `find ~/.coremcp/backups -name 'coremcp-*.sqlite3' -mtime -7` 으로 7일치 후보 일람.

### DR-3 keychain ↔ file vault 전환

새 호스트로 이주하거나 Keychain 접근이 어려운 환경 (CI, SSH, headless) 으로 옮길 때:

```bash
# 1) 현재 백엔드 + 자료 확인
cd apps/api && uv run coremcp doctor --api-url http://127.0.0.1:8787
# 2) 전체 자료를 단일 tarball 로 export (DB + secrets + Fernet 키 + admin token)
make cli-backup-export   # → ~/.coremcp/backups/coremcp-cli-export.tar
# 3) 새 호스트에서 백엔드 환경변수 결정 후 import
COREMCP_VAULT_BACKEND=keychain   # 또는 file
make cli-backup-import-dry-run   # dry-run 으로 충돌 확인
cd apps/api && uv run coremcp import --from ~/.coremcp/backups/coremcp-cli-export.tar
# 4) 검증
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.coremcp.api.plist
curl -fsS http://127.0.0.1:8787/ready
```

자동 회귀: `make backup-restore-drill` 이 임시 DB 로 위 흐름의 핵심 (백업→복원→해시 일치) 을 1분 안에 검증한다. CI 의 `restore-drill` job 도 동일 검증을 PR 단계에서 자동 실행.

## 5.7 Redis rate limiter 운영 (옵션)

기본은 `InMemoryRateLimiter` (단일 프로세스). 멀티 워커 / 멀티 호스트 배포 시 `RedisRateLimiter` 로 전환 가능. ADR-044 참조.

### 5.7.1 환경 변수

```bash
COREMCP_RATE_LIMIT_BACKEND=redis       # default "memory"
COREMCP_RATE_LIMIT_REDIS_URL=redis://127.0.0.1:6379/0
```

URL 미설정 또는 redis-py import 실패 시 자동으로 in-memory fallback (한 번 warning 로그). 운영 중에도 `RedisRateLimiter wire failure` 키워드로 fallback 발생 확인 가능.

### 5.7.2 Tailscale 위 Redis 공유 (개인 사용 권장)

```bash
# 운영 호스트 (Redis 서버)
brew install redis
redis-server --bind 127.0.0.1 --requirepass "$(openssl rand -hex 24)"
tailscale serve --bg --tcp 6379 tcp://localhost:6379

# CoreMCP 호스트
export COREMCP_RATE_LIMIT_REDIS_URL=redis://:<password>@<tailnet-host>:6379/0
export COREMCP_RATE_LIMIT_BACKEND=redis
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.coremcp.api.plist
```

ACL 은 `tailscale serve --remove` 로 항상 회수 가능. 팀/멀티 사용자는 mTLS 또는 별도 VPC + IAM 권장 — Tailscale serve 는 1인용.

### 5.7.3 연결 검증

```bash
# 1) Redis 자체 ping
redis-cli -u "$COREMCP_RATE_LIMIT_REDIS_URL" ping  # → PONG

# 2) CoreMCP rate limiter 가 Redis 사용 중인지 로그에서 확인
grep -i "RedisRateLimiter" ~/Library/Logs/coremcp/api.log | tail
# fallback 발생 시: "RedisRateLimiter falling back to in-memory: ..."
```

### 5.7.4 장애 대응

Redis 가 일시 장애일 때 `RedisRateLimiter.check()` 의 pipeline 호출이 예외를 던지면 **자동으로 InMemoryRateLimiter fallback** + warning 로그. 운영 영향 0 — 단, multi-host 정합성은 그 시간 동안 무효이므로 다음 두 가지 점검:

- Redis 복구 시 자동 회복 — 별도 재기동 불필요
- 장애 윈도우 동안의 한도 위반 의심 — `audit_logs` 와 `rate_limit_response` log 비교

자동 회귀: `tests/test_redis_rate_limiter.py` 가 fakeredis 로 INCR/EXPIRE + fallback path 를 검증한다. CI 통과 = 본 운영 path 안정.

---

## 6. Tailscale 외부 노출 (옵션)

### 6.1 설치
```bash
brew install --cask tailscale
# 또는 Mac App Store
tailscale up
```

### 6.2 Tailscale Serve (HTTPS)
```bash
tailscale serve --bg --https=443 http://localhost:8787
```

이후 `https://macmini.<tailnet>.ts.net` 으로 접근.

로컬 운영 smoke:

```bash
infra/scripts/ops-smoke.sh --require-tailscale
```

2026-05-14 현재 검증 머신에는 `tailscale` CLI가 설치되어 있지 않아 Tailscale install/login/Serve/ACL smoke는 skipped 상태다.

### 6.3 ACL
admin console에서 본인 디바이스만 접근 허용.

### 6.4 Caddy 대안
```text
macmini.local {
  reverse_proxy localhost:8787
}
```

mDNS + 자체 cert로 운영.

---

## 7. Runbooks

### 7.1 API 시작 실패
증상: launchd ALWAYS restarting, /health 응답 없음.

조치:
1. `launchctl print gui/$(id -u)/com.coremcp.api` 상태 확인
2. `tail -100 ~/.coremcp/logs/launchd.api.stderr`
3. `uvicorn` 명령 수동 실행 → 에러 원인 파악
4. 환경 변수 / DB 파일 경로 확인
5. 임시 fix 후 `launchctl kickstart -k`

### 7.2 Keychain 잠금
증상: tools/call에서 credential resolve 실패, log에 `keychain locked`.

조치:
1. Mac mini login
2. `security unlock-keychain login.keychain`
3. 자동 로그인 옵션 활성 검토 (System Settings → Users → Auto login)
4. fallback: fernet backend로 전환 (ADR-031: SECRET_BACKEND=fernet)

### 7.3 Downstream 장애
증상: 특정 service의 tools/call 연속 실패.

조치:
1. Web UI Service Detail / Logs 탭에서 에러 코드 확인
2. POST /v1/mcp-services/{id}/validate 재실행
3. credential 만료 시 rotate
4. downstream 서비스 자체 status 확인
5. 일시 disable 후 복구 시 enable

### 7.4 SQLite 락
증상: `database is locked` 에러.

조치:
1. WAL 모드 확인: `sqlite3 db.sqlite3 'PRAGMA journal_mode'`
2. `*.sqlite3-shm`, `*.sqlite3-wal` 파일 정상 여부
3. 동시 write 프로세스 식별 (launchd kickstart 중복 등)
4. 한 프로세스만 살리고 나머지 종료
5. 손상 의심 시 `.recover` 또는 backup 복원

### 7.5 디스크 풀
증상: 디스크 < 1GB.

조치:
1. `du -sh ~/.coremcp/{logs,data,backups,exports}`
2. 오래된 backup / log 정리
3. tool_invocations cleanup job 강제 실행
4. debug_traces 정리

### 7.6 Admin Token 분실 / 노출

증상: ~/.coremcp/admin-token 파일 삭제 또는 노출 의심.

조치 (admin token):
1. Mac mini 로컬에서 새 admin token 생성:
   `python -c "import secrets; print('cmcp_admin_' + secrets.token_urlsafe(32))" > ~/.coremcp/admin-token`
2. chmod 600 ~/.coremcp/admin-token
3. launchctl kickstart -k gui/$(id -u)/com.coremcp.api
4. Web UI 재로그인 (이전 sessionStorage 무효)
5. 모든 client token 일괄 revoke 검토 (admin token 노출 시 권장)

조치 (client token):
1. Web UI Settings/Tokens에서 해당 client token revoke
2. Claude Code 등 해당 client 재등록 (새 client token 발급)
3. external_connection 자체를 revoke할지 token만 revoke할지 정책 결정

audit log 확인:
- `client_token.issue`, `client_token.revoke`, `admin_token.rotate` 이벤트 grep

### 7.7 Schema Drift Avalanche
증상: 여러 service의 schema_hash가 동시에 변경.

조치:
1. 의도된 변경인지 본인 회상
2. UI에서 schema diff 검토
3. 자동 trust 정책 미설정이면 사용자 명시 confirm 후 catalog 갱신

### 7.8 Tailscale 외부 401
증상: MacBook에서 401.

조치:
1. token 동기화 확인 (Mac mini의 token과 MacBook sessionStorage가 일치)
2. 회전 후 재등록 누락 여부
3. Tailscale ACL이 외부 디바이스 차단했는지

---

## 8. SLO (개인 컨텍스트)

목표 (본인 만족 기준):

| SLO | Target |
|---|---|
| `/mcp` availability (24h window) | > 99% |
| tools/list p95 (cache hit) | < 200ms |
| tools/call gateway overhead p95 | < 100ms |
| 재부팅 후 정상화 시간 | < 5분 |
| backup 성공률 | 100% (daily) |
| schema drift 알림 지연 | < 5분 |

장애 시 본인 알림 (옵션):
- macOS notification (terminal-notifier)
- 또는 단순히 dashboard 빨간 배지

---

## 9. Maintenance 체크리스트

아래 항목은 운영자가 계속 반복하는 maintenance calendar다. 체크가 비어 있어도 코드가 빠졌다는 의미가 아니며, 실제 운영 환경에서 수행/기록할 때마다 갱신한다.

### 매일
- [ ] Web UI Dashboard 상태 확인 (수동)

### 매주
- [ ] `~/.coremcp/logs/` 용량 확인
- [ ] 최근 invocation 에러율 확인
- [ ] credential 만료 임박 알림 확인

### 매월
- [ ] backup 복원 dry-run
- [ ] dependency 업데이트 (`uv sync`, `pnpm update`)
- [ ] CVE scan (`uv tree | grep -i critical` 등 수작업 또는 자동)
- [ ] Mac mini Time Machine 점검
- [ ] admin token chmod 확인 (600)
- [ ] client token 30일 inactive 목록 검토 + 정리

### 분기
- [ ] admin token 회전 (이전: personal token 회전)
- [ ] client token 일괄 revoke 권장 (선택)
- [ ] downstream credential 회전
- [ ] 환경 변수 점검 (rotation, key expiry)
- [ ] SSRF allowlist 환경 변수(ALLOW_TAILSCALE_DOWNSTREAM / ALLOWED_PRIVATE_CIDRS) 변경 이력 검토 (ADR-033)

---

## 10. 개인 컨텍스트라 제외하는 운영 영역

production_docs_donotuse/12-operations-observability.md의 다음은 본 프로젝트에 적용 안 됨:
- Multi-region failover
- Cross-region backup
- Incident Response severity matrix (SEV1~4)
- Postmortem template (본인용 간단 메모로 대체)
- Deploy strategy blue/green canary (단일 host)
- Status page (외부 사용자 없음)
- Customer Success dashboard
- Cost observability (downstream API 비용은 본인 청구서 확인)
- Compliance dashboard
- On-call rotation
- Sentry / Datadog production tier (옵션 free tier만)
- PagerDuty / Opsgenie
- SOC2 audit

## 11. External Validation / Soak / Mobile QA Helpers

2026-05-14 추가 상태:

- `make external-env-validate`: local `ops-smoke`를 재사용하고, `COREMCP_EXTERNAL_API_URL` / `COREMCP_EXTERNAL_WEB_URL`이 주어지면 Tailscale/Caddy 외부 URL까지 확인한다.
- `make soak-check`: 지정 시간 동안 `/ready`와 `/v1/dashboard/summary`를 반복 확인해 long soak의 실패 횟수를 종료 코드로 신호한다.
- `make mobile-qa-checklist`: 실제 모바일 브라우저에서 확인할 URL과 점검 순서를 출력한다.

대표 실행:

```bash
# 로컬 launchd/ops smoke + 선택적 외부 URL 확인
make external-env-validate
COREMCP_EXTERNAL_API_URL=https://<tailscale-or-public-host> \
COREMCP_EXTERNAL_WEB_URL=https://<tailscale-or-public-host> \
  make external-env-validate

# 실제 모바일 기기에서 열 URL과 수동 점검 항목 출력
make mobile-qa-checklist

# long soak; 운영 환경에 맞게 시간/간격 조정
COREMCP_SOAK_DURATION_SECONDS=3600 \
COREMCP_SOAK_INTERVAL_SECONDS=30 \
  make soak-check
```

실제 reboot, Tailscale 로그인/ACL, real external OAuth client compatibility는 환경 의존 검증이므로 운영 host에서 위 helper를 실행해 결과를 기록한다.

이번 안정화 batch의 code hardening(STDIO resource limits, admin/MCP rate limit, CLI import hardening)은 통합 완료했다. 로컬 검증 결과는 `../TESTING.md`의 2026-05-14 snapshot을 따른다. 실제 reboot, Tailscale Serve/ACL, real external OAuth client, 모바일 기기 QA, long soak은 운영 host에서 별도 기록한다.

### 11.1 운영 host 검증 순서

이 섹션은 미구현 backlog가 아니라 실제 Mac mini/Tailscale/OAuth/mobile 환경에서 수행하는 validation runbook이다. 로컬 개발 머신에서 실제 재부팅, Tailscale login/ACL 변경, 외부 OAuth client 등록, 실제 모바일 기기 QA를 대리 수행하지 않는다.

1. **사전 준비**
   - API/Web/backup/logrotate/refresh launchd label을 load한다.
   - admin/client token은 운영 host의 vault/token file만 사용한다.
   - 결과 저장 경로를 만든다: `mkdir -p dev-plan/.artifacts/external-env/$(date +%Y%m%d-%H%M%S)`.
2. **post-reboot recovery**
   - 운영 host를 실제 재부팅한 뒤 5분 이내 실행: `infra/scripts/external-env-validate.sh --post-reboot`.
   - `launchctl`, `/ready`, Web 200, backup/logrotate/refresh label 상태를 evidence log로 저장한다.
3. **Tailscale URL validation**
   - Tailscale login/Serve/ACL은 운영자가 수동 설정한다.
   - `COREMCP_EXTERNAL_API_URL=https://<tailnet-host> COREMCP_EXTERNAL_WEB_URL=https://<tailnet-host> make external-env-validate`를 실행한다. API env는 host/base URL만 넣고 script가 `/ready`를 붙인다.
   - ACL은 본인 디바이스만 접근 가능한지 별도 브라우저/기기에서 확인한다.
4. **real OAuth client compatibility**
   - `AUTH_MODE=oauth`를 활성화한 운영 host에서 실제 client의 redirect URI, scope, CIMD/DCR 지원 여부를 기록한다.
   - authorize/code/token/refresh/revoke가 통과하는지 확인하되, client secret/token 원문은 evidence에 저장하지 않는다.
5. **mobile QA**
   - `COREMCP_WEB_URL`/`COREMCP_API_URL`을 모바일에서 접근 가능한 URL로 설정하고 `make mobile-qa-checklist`를 실행한다.
   - 실제 iOS/Android 브라우저에서 Dashboard, Services, Toolbox, Clients, Settings, Playground, Logs를 확인한다.
6. **long soak**
   - 운영 시간에 맞게 `COREMCP_SOAK_DURATION_SECONDS=3600 COREMCP_SOAK_INTERVAL_SECONDS=30 make soak-check`를 실행한다.
   - stdout JSONL을 evidence로 저장하고 exit code, failure count, latency 추세를 `TESTING.md` template에 기록한다.

Exit-code 기준:
- `external-env-validate`: `0`이면 local/external configured checks 통과, `64`는 인자 오류, `1`은 필수 smoke 실패. 외부 URL 미설정은 warning + skip이며 실패가 아니다.
- `soak-check`: `0`이면 허용 실패 횟수 이내, `1`이면 `/ready`/dashboard 반복 확인이 실패 한도를 초과.
- `mobile-qa-checklist`: `0`이면 체크리스트 출력 성공이며, 실제 pass/fail은 운영자가 기기에서 수동 기록한다.

결과 기록 양식은 [`../TESTING.md`](../TESTING.md)의 “External environment result record template”을 사용한다.
