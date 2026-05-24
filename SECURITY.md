# Security Policy

CoreMCP is a personal MCP gateway for a single operator. Security reports are welcome for the gateway, Web Admin, bundled MCP services, local operations scripts, and deployment helpers in this repository.

CoreMCP는 개인 운영자를 위한 MCP gateway입니다. 이 저장소의 gateway, Web Admin, 번들 MCP 서비스, 로컬 운영 스크립트, 배포 보조 도구에 대한 보안 제보를 환영합니다.

---

## Supported Scope

| Area | Status | Security support |
|---|---:|---|
| CoreMCP `main` branch | Active | Yes |
| CoreMCP tagged/RC releases | Active if recently published | Best effort |
| Web Admin | Active | Yes |
| Bundled Project Docs / Git Workspace MCP services | Active | Yes |
| Experimental demo MCP services | Demo/test only | Best effort |
| `production_docs_donotuse/` | Reference only | No runtime support |

If a vulnerability affects a committed release candidate, mention the exact commit SHA or tag in the report.

> Note: Coreline Auth is maintained as a separate independent module repository: `https://github.com/coreline-ai/coreline-auth-module`. Use that repository's `SECURITY.md` for module-specific reports.

---

## Reporting a Vulnerability

Please do **not** open a public issue containing exploit details, tokens, private URLs, logs with secrets, or screenshots that expose credentials.

Preferred reporting path:

1. Use GitHub **Private vulnerability reporting / Security Advisory** for this repository if available.
2. If private advisory is not available, contact the repository owner privately and include only a minimal public issue such as “Security report available privately”.
3. Include reproduction steps, affected commit/tag, expected impact, and suggested fix if known.

Recommended report template:

```text
Title:
Affected area: CoreMCP API / Web Admin / bundled MCP service / infra / docs
Affected version or commit:
Severity estimate: Critical / High / Medium / Low
Environment: OS, Python, Node, browser, AUTH_MODE, transport_type
Summary:
Reproduction steps:
Impact:
Logs or screenshots: redact all tokens/secrets
Suggested mitigation:
```

We aim to acknowledge credible reports quickly and prioritize fixes by severity. Exact response time can vary because this is a personal project.

---

## Please Redact Before Sending

Never include raw values for:

- `cmcp_admin_*` admin tokens
- `cmcp_client_*` client tokens
- `Authorization`, `X-API-Key`, OAuth access/refresh/id tokens
- downstream MCP credentials
- `FERNET_KEY`, Keychain values, SMTP credentials
- Project Docs / Git Workspace private filesystem paths when not necessary
- private Tailscale hostnames or ACLs unless strictly required and redacted

Use placeholders such as `<redacted-token>` or `<redacted-host>`.

---

## CoreMCP Security Model

CoreMCP is designed as a protected personal gateway, not a public multi-tenant SaaS platform.

Core invariants:

- Every `/mcp` request re-checks bearer authentication.
- `Mcp-Session-Id` is routing state only, never authentication.
- CoreMCP admin/client tokens must never be forwarded downstream.
- Downstream credentials must go through the vault abstraction.
- Downstream HTTP URLs must pass SSRF checks.
- STDIO downstream commands must pass the configured command allowlist.
- Tool icons are rendered through `<img src=...>` only; inline SVG is not allowed.
- Raw tool arguments/results are not stored unless debug tracing is explicitly enabled.
- `AUTH_MODE=static_bearer` remains the default; OAuth/CIMD/DCR is optional.

For detailed design notes, see:

- [`coremcp-docs/06-security-auth.md`](./coremcp-docs/06-security-auth.md)
- [`AGENTS.md`](./AGENTS.md)
- [`TESTING.md`](./TESTING.md)

---

## Bundled MCP Service Security Notes

### Project Docs MCP

Project Docs MCP is intended for local, read-oriented project documentation discovery. Reports are in scope when they involve path traversal, symlink escape, unexpected file disclosure outside the configured root, or unsafe content handling.

### Git Workspace MCP

Git Workspace MCP is intended for local repository inspection and controlled workspace operations. Reports are in scope when they involve repository root escape, unsafe Git ref handling, command injection, sensitive stderr leakage, or commit subject disclosure that bypasses redaction expectations.

---

## Out of Scope / Not a Vulnerability by Itself

The following are generally not considered security vulnerabilities unless they can be chained to a meaningful impact:

- Lack of SaaS/team/workspace/marketplace/billing features.
- Localhost-only access assumptions in development mode.
- Missing OAuth provider credentials in demo mode.
- Demo MCP services returning synthetic data.
- Denial-of-service requiring local admin access to intentionally register many expensive tools.
- Reports requiring already-compromised local admin token without a new privilege boundary crossing.

Still report suspicious behavior if it violates the invariants above.

---

## Local Security Checklist for Operators

Before exposing CoreMCP beyond localhost:

- Use HTTPS through a trusted tunnel/proxy such as Tailscale or equivalent.
- Keep admin/client tokens out of shell history and screenshots.
- Use per-client CoreMCP client tokens instead of sharing the admin token.
- Store downstream credentials through Keychain/Fernet vault only.
- Keep `COREMCP_STDIO_ALLOWED_COMMANDS` narrow.
- Avoid registering untrusted STDIO MCP servers.
- Run smoke checks after upgrades.

Recommended commands:

```bash
make test
make ui-smoke
make ui-smoke-p0
make external-env-validate
```

---

## Dependency and Supply Chain Notes

- Prefer pinned lockfiles and reproducible installs.
- Review new dependencies before adding them, especially auth, crypto, OAuth/OIDC, browser automation, and STDIO execution dependencies.
- Do not add external LLM API dependencies to CoreMCP runtime.
- Treat community MCP servers as untrusted code unless reviewed.

---

## Disclosure and Fix Process

Typical handling flow:

1. Triage report and reproduce in a local environment.
2. Classify severity and affected scope.
3. Patch with regression tests.
4. Run relevant smoke tests.
5. Publish a fix commit/release note without exposing exploit details prematurely.
6. Credit reporter if they want attribution.

Thank you for helping keep CoreMCP safe.
