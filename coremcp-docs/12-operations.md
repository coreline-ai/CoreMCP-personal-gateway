# CoreMCP Operations (Personal)

문서 버전: v1.0
작성일: 2026-05-11

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
별도 plist `com.coremcp.web` — Next.js build 후 `next start --port 3000` 또는 정적 export.

### 2.3 Worker daemon (옵션)
Arq 사용 시 `com.coremcp.worker` 추가. MVP는 API 프로세스 내 BackgroundTasks.

---

## 3. Logs

### 3.1 위치
- API stdout/stderr: `~/.coremcp/logs/launchd.api.{stdout,stderr}`
- structured JSON: `~/.coremcp/logs/coremcp.log` (structlog)
- web Next.js: `~/.coremcp/logs/launchd.web.{stdout,stderr}` (옵션)
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
crontab 또는 launchd로 daily:
```bash
# infra/scripts/rotate-logs.sh
LOG_DIR=~/.coremcp/logs
find "$LOG_DIR" -name 'coremcp.log.*.gz' -mtime +7 -delete
DATE=$(date +%Y%m%d)
if [ -f "$LOG_DIR/coremcp.log" ]; then
  mv "$LOG_DIR/coremcp.log" "$LOG_DIR/coremcp.log.$DATE"
  gzip "$LOG_DIR/coremcp.log.$DATE"
  launchctl kickstart -k gui/$(id -u)/com.coremcp.api  # 새 파일 생성
fi
```

크론:
```bash
0 0 * * * ~/projects/coremcp/infra/scripts/rotate-logs.sh
```

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

본인용 주요 metric:
- `mcp_requests_total{method,status}`
- `mcp_request_duration_ms{method}`
- `downstream_requests_total{service,status}`
- `downstream_latency_ms{service}`
- `tool_invocations_total{service,status}`
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
# infra/scripts/backup.sh
DEST=~/.coremcp/backups
mkdir -p "$DEST"
DATE=$(date +%Y%m%d-%H%M%S)
sqlite3 ~/.coremcp/data/db.sqlite3 ".backup '$DEST/db.$DATE.sqlite3'"
gzip "$DEST/db.$DATE.sqlite3"
# 7일 이상 된 backup 삭제
find "$DEST" -name 'db.*.sqlite3.gz' -mtime +7 -delete
```

크론:
```bash
0 3 * * * ~/projects/coremcp/infra/scripts/backup.sh
```

### 5.2 전체 디렉토리 backup
- Time Machine으로 `~/.coremcp/` 포함
- 또는 iCloud Drive 동기화 (~/.coremcp가 ~/Library/Mobile Documents 아래 심볼릭 링크 옵션)
- 또는 rsync to NAS

### 5.3 Credential backup
- macOS Keychain은 iCloud Keychain 활성 시 자동 동기화
- Fernet master key는 별도 안전한 곳에 보관 (1Password 등)

### 5.4 Backup 복원
```bash
launchctl bootout gui/$(id -u)/com.coremcp.api
gunzip < ~/.coremcp/backups/db.20260511-030000.sqlite3.gz > ~/.coremcp/data/db.sqlite3
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.coremcp.api.plist
```

RPO 목표: 24시간
RTO 목표: 2시간

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
4. Web UI 재로그인 (이전 localStorage 무효)
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
1. token 동기화 확인 (Mac mini의 token과 MacBook localStorage가 일치)
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

production_docs/12-operations-observability.md의 다음은 본 프로젝트에 적용 안 됨:
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
