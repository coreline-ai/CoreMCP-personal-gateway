# CoreMCP Security Best Practices Review

Review date: 2026-05-16  
Reviewed base commit: `86ef5f6 docs(security): record hardening review status` + follow-up working tree hardening
Scope: personal CoreMCP gateway only — `apps/api/coremcp` FastAPI backend, MCP gateway/proxy, credential vault, OAuth, STDIO transport, `apps/web` Next.js Web Admin, selected infra scripts. SaaS/team/workspace/marketplace/publisher/billing features are explicitly out of scope.

## Executive summary

No Critical / P0 vulnerability was found in the current code review. The core personal-gateway security invariants are largely well implemented: CoreMCP tokens are not forwarded downstream, `/mcp` bearer auth is rechecked per request, downstream credentials go through the vault, HTTP downstream URLs have SSRF controls, STDIO env is sanitized, CSP is nonce-based, and raw tool arguments/results are not persisted by default.

Hardening batch status:

| ID | Area | Batch status | Notes |
|---|---|---|---|
| S-02 | `TrustedHostMiddleware` / allowed hosts | Patched in this batch | `COREMCP_ALLOWED_HOSTS` + `TrustedHostMiddleware` + host regression tests. |
| S-07 | value-based redaction | Patched in this batch | Structlog/audit metadata value-pattern redaction + regression tests. |
| S-01 | OAuth consent / client allow policy | Mitigated in follow-up batch | DCR disable toggle + client_id allowlist policy hook. Consent UI intentionally not added. |
| S-04 | STDIO argv profiles | Patched in follow-up batch | Dangerous interpreter/docker argv profiles rejected before spawn. |
| S-05 | remote icon proxy / opt-in | Patched in follow-up batch | Remote HTTPS icons are default-off with explicit opt-in. |
| S-06 | allowlist DNS pinning | Patched in follow-up batch | Allowlisted hosts are resolved, DNS-change checked, and IP-pinned when resolvable. |

Residual risks are operational / exposure-dependent and should remain bounded to the personal gateway model.

## Strengths confirmed

- **Per-request MCP auth**: `/mcp` verifies client/OAuth/admin bearer on each request (`apps/api/coremcp/main.py:192-217`; `apps/api/coremcp/api/mcp_endpoint.py:28-68`).
- **Mcp-Session-Id is not auth**: it is used for session routing/touch only (`apps/api/coremcp/api/mcp_endpoint.py:49-63`, `102-107`).
- **Downstream token boundary**: incoming CoreMCP `Authorization` is not copied; only explicit vault-backed downstream credentials and idempotency headers are allowed (`apps/api/coremcp/proxy/downstream.py:86-92`).
- **STDIO env token boundary**: parent env is not inherited and auth-like variables are stripped (`apps/api/coremcp/proxy/stdio.py:25-31`, `53-56`; `apps/api/coremcp/main.py:344-360`).
- **SSRF guard**: blocks private/link-local/metadata addresses, disallows redirects, and IP-pins non-allowlist public DNS destinations (`apps/api/coremcp/proxy/security.py:58-117`; `apps/api/coremcp/proxy/downstream.py:94-123`, `267-291`).
- **Credential vault**: Fernet encryption is active for file backend; Keychain insertion uses stdin rather than exposing secret as a process argument (`apps/api/coremcp/credentials/vault.py:110-155`, `218-232`).
- **OAuth token hardening**: RS256 JWT validation enforces issuer/audience; refresh token rotation detects reuse and revokes the family (`apps/api/coremcp/auth/oauth.py:275-322`, `343-353`).
- **Request/response limits**: app-level `Content-Length` and streaming request body limit exist, plus downstream max response size (`apps/api/coremcp/settings.py:49-52`; `apps/api/coremcp/api/body_limit.py:28-51`; `apps/api/coremcp/proxy/downstream.py:188-199`).
- **Rate limits**: admin, MCP, service, OAuth DCR/CIMD paths have in-process fixed-window limiters (`apps/api/coremcp/settings.py:63-65`; `apps/api/coremcp/main.py:1685-1701`; `apps/api/coremcp/mcp/tools_handlers.py:526-560`).
- **Web CSP**: production CSP avoids `unsafe-inline`; scripts are nonce-gated; frame/object/base restrictions are present (`apps/web/middleware.ts:3-19`, `35-39`).
- **Frontend XSS sinks**: scan did not find `dangerouslySetInnerHTML`, `innerHTML`, `eval`, `new Function`, or `postMessage` handlers under `apps/web`.
- **Audit minimization**: tool invocation records store metadata/status, not raw tool arguments/results (`apps/api/coremcp/db/repository_audit.py:74-130`; `apps/api/coremcp/mcp_gateway/idempotency.py:9-15`).

## Findings

### S-01 — High if OAuth is externally reachable: OAuth authorize has no user consent/admin approval gate

- **Rule ID:** FASTAPI-AUTHZ-001 / OAuth consent boundary
- **Severity:** High when `AUTH_MODE=oauth` is reachable from untrusted clients; Medium for local-only/Tailscale ACL environments
- **Batch status:** Mitigated in follow-up batch — policy hook added, consent UI intentionally out of scope
- **Location:** `apps/api/coremcp/api/oauth.py:139-205`; `apps/api/coremcp/settings.py:71`
- **Evidence:**
  - DCR is unauthenticated and only rate-limited (`/oauth/register`, lines 139-174).
  - `/oauth/authorize` validates parameters and immediately issues a code + redirect; no local admin approval / consent UI exists (lines 176-205).
  - `AUTH_MODE=static_bearer` remains the default (`settings.py:71`), which limits default exposure.
- **Impact:** If OAuth mode is enabled and the gateway is reachable by an untrusted browser/client, a client can self-register and complete an authorization-code + PKCE flow without a human consent step. This can mint access tokens for CoreMCP scopes.
- **Patch evidence:** `COREMCP_OAUTH_DCR_ENABLED=false` disables `/oauth/register`; `COREMCP_OAUTH_ALLOWED_CLIENT_IDS` enforces `client_id` allowlist on `/oauth/authorize` and `/oauth/token`; tests cover no-store OAuth errors and allowed/blocked clients.
- **Next-work direction:** Keep OAuth disabled by default. Before external OAuth exposure, set DCR disabled or a narrow client allowlist. A browser consent UI remains optional future work, not part of the personal gateway hardening batch.
- **Mitigation:** Document that OAuth mode is for controlled clients only until consent exists; require Tailscale ACL or loopback binding for `/oauth/*`.
- **False positive notes:** This is acceptable for personal local-only usage if network reachability is strictly controlled.

### S-02 — Medium: Host header is not constrained while OAuth metadata derives issuer/resource from request host

- **Rule ID:** FASTAPI-DEPLOY-BASELINE / TrustedHost
- **Severity:** Medium
- **Batch status:** Patched in this batch — `COREMCP_ALLOWED_HOSTS` and `TrustedHostMiddleware` implemented
- **Location:** `apps/api/coremcp/main.py:1642-1658`; `apps/api/coremcp/api/oauth.py:18-23`, `109-118`; `apps/api/coremcp/main.py:203-207`
- **Previous evidence:**
  - FastAPI app previously had no app-level host allowlist.
  - OAuth issuer/resource URLs are computed from `request.base_url` and `request.url_for(...)` (`api/oauth.py:18-23`, `109-118`).
  - Access token verification also uses request-derived issuer/audience (`main.py:203-207`).
- **Patch evidence:**
  - `COREMCP_ALLOWED_HOSTS` and `allowed_host_list` are defined in `apps/api/coremcp/settings.py`.
  - `TrustedHostMiddleware` is registered in `apps/api/coremcp/main.py`.
  - `apps/api/tests/test_trusted_hosts.py` covers allowed/default/custom hosts and rejected host behavior.
- **Impact:** If the API is exposed directly or via a proxy that does not sanitize `Host` / forwarded headers, hostile hosts can influence generated OAuth metadata and issuer/resource calculations. This can cause metadata poisoning or token audience/issuer confusion in OAuth mode.
- **Fix applied:** Add `COREMCP_ALLOWED_HOSTS` and Starlette `TrustedHostMiddleware` for API production/Tailscale exposure. Default covers personal local/test operation: `localhost`, `127.0.0.1`, `::1`, and `testserver`; Tailscale/custom hostnames must be supplied explicitly.
- **Verification:** invalid `Host` receives 400; valid default/custom host still serves `/health`.
- **Residual mitigation:** Ensure Caddy/Tailscale Serve strips or controls Host, and include the external hostname in `COREMCP_ALLOWED_HOSTS`.
- **False positive notes:** If the API only listens on localhost behind a trusted local web UI, this is lower risk.

### S-03 — Medium: Web Admin stores admin token in `sessionStorage`

- **Rule ID:** JS-STORAGE-001
- **Severity:** Medium
- **Batch status:** Accepted for personal/local model; not in current batch
- **Location:** `apps/web/lib/api.ts:253-260`; `apps/web/components/admin/sections/settings-section.tsx:17`
- **Evidence:** `getStoredAdminToken()` and `saveAdminToken()` read/write `coremcp_admin_token` in browser `sessionStorage`.
- **Impact:** Any Web Admin XSS would be able to read and exfiltrate the admin token. Current CSP and lack of dangerous DOM sinks reduce the probability, but the impact remains high if XSS occurs.
- **Fix:** For external exposure, consider an httpOnly same-site cookie session established via local admin token exchange, or a short-lived in-memory-only admin token mode.
- **Mitigation:** Keep nonce CSP strict, keep `dangerouslySetInnerHTML` banned, avoid third-party scripts, keep Web Admin behind Tailscale/localhost, clear token on unauthorized responses.
- **False positive notes:** `sessionStorage` is better than `localStorage` and is documented in UI; acceptable for current personal/local admin console if CSP stays strict.

### S-04 — Medium/Low: STDIO command allowlist is basename-only and does not constrain dangerous arguments

- **Rule ID:** Command execution defense-in-depth
- **Severity:** Medium if admin token can be stolen; Low if only trusted local admin can register services
- **Batch status:** Patched in follow-up batch — argv profile deny layer added
- **Location:** `apps/api/coremcp/settings.py:14`, `59-62`; `apps/api/coremcp/proxy/stdio.py:48-50`
- **Evidence:** Default allowlist includes interpreters/runtimes (`npx,uvx,python,python3,node,docker,deno`), and enforcement checks only `Path(command[0]).name`.
- **Impact:** A compromised admin token or malicious operator action can still run commands such as `python -c ...`, `node -e ...`, or powerful `docker` invocations. This is not privilege escalation against a trusted admin, but it weakens defense-in-depth for exposed admin surfaces.
- **Patch evidence:** `validate_stdio_argv_profile()` rejects `python -c`, `node -e/--eval/-p/--print`, `deno eval`, and dangerous Docker host/volume/socket options while preserving common `npx`, `uvx`, `python server.py`, and `node server.js` launches.
- **Residual direction:** This is still a deny-profile defense layer, not a sandbox. macOS sandbox/container isolation remains a separate long-term ADR if needed.
- **Mitigation:** Keep STDIO services admin-only, audit `service.stdio_command_rejected`, keep allowlist small per deployment, and document that STDIO is host-code execution.
- **False positive notes:** CoreMCP correctly avoids `shell=True` and sanitizes env; this finding is about sandboxing depth, not an immediate injection bug.

### S-05 — Low/Medium: Remote tool icons can leak operator IP/timing to downstream-controlled hosts

- **Rule ID:** Frontend privacy / third-party resource loading
- **Severity:** Low to Medium
- **Batch status:** Patched in follow-up batch — remote HTTPS icon opt-in added
- **Location:** `apps/api/coremcp/registry/catalog.py:53-96`; `apps/web/middleware.ts:15`; `apps/web/components/tool-icon.tsx:33-45`
- **Evidence:** Tool icon metadata accepts `https://` and `data:image/` sources; Web CSP allows `img-src 'self' data: https:`; Web Admin renders icon URLs directly in `<img>`.
- **Impact:** A downstream MCP service can cause the admin browser to load an arbitrary HTTPS image. `Referrer-Policy: no-referrer` reduces URL leakage, but the remote host still sees the operator IP, user-agent, and timing. This is a privacy/tracking concern, not server-side SSRF.
- **Patch evidence:** `COREMCP_REMOTE_TOOL_ICONS_ENABLED=false` by default drops remote HTTPS icons during catalog normalization; `true` explicitly opts in. Rendering still uses `src` and `<img>` only; inline SVG remains forbidden.
- **Residual direction:** Same-origin icon proxy/cache can be considered later if remote icon UX is required without browser privacy leakage.
- **Mitigation:** SVG is disabled by default (`ICON_SVG_ENABLED=false`), and icons are rendered via `<img>` only, which is good.
- **False positive notes:** If all downstream services are trusted/local, this is low risk.

### S-06 — Low/Medium: SSRF host allowlist bypasses DNS pinning by design

- **Rule ID:** SSRF hardening
- **Severity:** Low to Medium, depending on use of `COREMCP_SSRF_ALLOW_HOSTS`
- **Batch status:** Patched in follow-up batch — allowlisted host resolve/DNS-change check/IP pinning added
- **Location:** `apps/api/coremcp/proxy/security.py:62-73`, `97-117`; `apps/api/coremcp/proxy/downstream.py:267-291`
- **Previous evidence:** If `host in ssrf_allow_hosts`, `UrlSafetyResult.resolved_ips` was empty and `_pinned_destination()` returned the original URL without IP pinning.
- **Impact:** If an allowlisted host is compromised or DNS changes unexpectedly, downstream HTTP requests may go to a private or unintended address without the same pinning protection used for public DNS.
- **Patch evidence:** Allowlisted hosts are resolved when possible, metadata IP remains blocked, before/after DNS set changes are rejected, and resolvable allowlisted hosts are IP-pinned while preserving original Host/SNI.
- **Residual direction:** Use `COREMCP_SSRF_ALLOW_HOSTS` sparingly; allowlist remains an explicit operator override for personal internal/Tailscale destinations.
- **Mitigation:** Use `COREMCP_SSRF_ALLOW_HOSTS` sparingly; prefer exact internal hostnames and stable Tailscale IP/CIDR policy.
- **False positive notes:** An allowlist is an explicit operator override; current behavior may be intentional for local/internal services.

### S-07 — Low: Log redaction is key-name based, not value-regex based

- **Rule ID:** Secret redaction defense-in-depth
- **Severity:** Low
- **Batch status:** Patched in this batch — value-based redaction now applies to structlog payloads and audit metadata
- **Location:** `apps/api/coremcp/logging.py:44-58`; `apps/api/coremcp/db/repository_audit.py:41-69`
- **Previous evidence:** Structlog redaction masked only values whose key contained `authorization`, `token`, `api_key`, `secret`, etc. Audit metadata was serialized directly by `log_audit()`.
- **Patch evidence:** `apps/api/coremcp/logging.py` exposes recursive `redact_value()` with token-like value patterns; `apps/api/coremcp/db/repository_audit.py` applies it before serializing audit metadata; `apps/api/tests/test_logging_redaction.py` covers neutral-key and nested metadata redaction.
- **Impact:** If a future caller puts a token-like value under a neutral key such as `value`, `text`, or `message`, it may be stored in logs/audit. Current reviewed callsites appear curated, but the helper does not enforce value-pattern redaction.
- **Fix applied:** Add value-based regex redaction for common token patterns (`cmcp_admin_`, `cmcp_client_`, `cmcp_refresh_`, `cmcp_otk_`, `cmcp_code_`, `sk-`, `ghp_`, JWT-like `eyJ...`) in both structlog and audit metadata paths.
- **Verification:** unit tests cover neutral-key values and nested metadata structures.
- **Residual mitigation:** Keep audit metadata callsites curated and avoid passing raw request bodies, tool args/results, or credential values to logs.
- **False positive notes:** No current callsite was found storing raw tokens in audit metadata.

## Recommended next actions

### Completed in hardening batches

1. **S-02 TrustedHostMiddleware:** allowed-hosts config and tests landed without changing auth semantics.
2. **S-07 value-based redaction:** recursive value-pattern redaction landed for structlog/audit metadata.
3. **S-01 OAuth policy hook:** DCR disable toggle and client_id allowlist added without adding SaaS/user-consent scope.
4. **S-04 STDIO argv profiles:** dangerous interpreter/docker argv patterns are rejected before subprocess spawn.
5. **S-05 Remote icon opt-in:** remote HTTPS tool icons are default-off; data/self fallback remains.
6. **S-06 Allowlist DNS pinning:** allowlisted hosts are resolved, rechecked, and pinned when possible.

### Remaining operational choices

1. **OAuth external exposure:** set `COREMCP_OAUTH_DCR_ENABLED=false` or `COREMCP_OAUTH_ALLOWED_CLIENT_IDS=...`; a consent UI is still out of scope.
2. **STDIO sandboxing:** argv profiles are not a sandbox; keep STDIO admin-only.
3. **Remote icon UX:** only enable remote icons for trusted downstreams, or implement a same-origin proxy/cache later.
4. **SSRF allowlist:** keep `COREMCP_SSRF_ALLOW_HOSTS` narrow and exact.

## Overall security rating

- Localhost-only personal operation: **A-**
- Tailscale private tailnet with strict ACL: **A-** when `COREMCP_ALLOWED_HOSTS` includes the external hostname
- Public internet exposure: **Not recommended** without OAuth DCR/client allowlist controls and Web Admin token/session model review
