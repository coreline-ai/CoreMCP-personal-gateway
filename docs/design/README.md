# CoreMCP Design System

문서 버전: `v1.1`
작성일: `2026-05-13`
대상: `apps/web` — CoreMCP Web Admin
참조 원본: `/Users/hwanchoi/projects/multica/multica-main/docs/design.md`

## 디자인 목표

CoreMCP Web Admin은 개인용 MCP Gateway를 운영하는 **local-first operations console**이다. 디자인은 화려한 SaaS marketing UI가 아니라, Mac mini에서 24/7 운영되는 gateway 상태를 빠르게 판단하고 조작하는 데 최적화한다.

이번 v1.1은 Multica 디자인 원칙을 실제 코드에 반영한 버전이다.

## Multica에서 흡수한 핵심 원칙

- **크게 꾸미지 않는다**: gradient, glow, shadow, 큰 hero title을 제거한다.
- **중립 surface 우선**: 화면 대부분은 `background/card/muted/border` token으로 구성한다.
- **색상은 신호로만 사용**: brand/emerald/amber/rose는 상태, 위험, action 구분에만 쓴다.
- **작은 타이포 체계**: `text-base`, `text-sm`, `text-xs`를 기본으로 하고 제목도 `font-medium`에 머문다.
- **일관된 interaction**: hover는 `muted`, active는 `muted + foreground/font-medium`, focus는 ring token으로 통일한다.
- **dark 기본 운영**: local-first admin console은 기본 dark theme로 시작하고 sidebar에서 Light/Dark/System을 선택한다.

## 코드 기준 source of truth

| 항목 | 파일 |
|---|---|
| Tailwind token mapping | `apps/web/tailwind.config.ts` |
| Global OKLCh token / component primitive | `apps/web/app/globals.css` |
| Admin shell / navigation | `apps/web/components/admin/admin-shell.tsx` |
| Route sections | `apps/web/components/admin/sections/*.tsx` |
| Service detail | `apps/web/components/admin/service-detail-console.tsx` |
| Tool icon policy | `apps/web/components/tool-icon.tsx` |

## 핵심 토큰

| Token | 값 | 용도 |
|---|---|---|
| `--background` | `oklch(1 0 0)` | 페이지 바탕 |
| `--foreground` | `oklch(0.141 0.005 285.823)` | 주요 텍스트 |
| `--card` | `oklch(1 0 0)` | 카드 surface |
| `--muted` | `oklch(0.967 0.001 286.375)` | hover/secondary surface |
| `--muted-foreground` | `oklch(0.552 0.016 285.938)` | 보조 텍스트 |
| `--border` | `oklch(0.92 0.004 286.32)` | 경계선 |
| `--primary` | `oklch(0.21 0.006 285.885)` | primary action |
| `--brand` | `oklch(0.55 0.16 255)` | 제한적 강조 |

Dark theme는 `.dark` class에 동일 token 이름으로 정의한다. 기본 HTML class는 `dark`이며, 사용자가 sidebar 하단 Theme selector에서 선택한 값은 `localStorage.coremcp_theme`에 저장한다.

전체 token asset은 [`assets/coremcp-theme.tokens.json`](./assets/coremcp-theme.tokens.json)을 참조한다.

## 화면 구조 원칙

0. **Multica app frame**
   - 전체 Web Admin은 좌측 sidebar + 상단 48px page header + route content pane 구조를 쓴다.
   - sidebar는 `Gateway / MCP / Connections / Configure` group으로 나눈다.
   - route는 자기 화면만 렌더링한다. 한 route에서 Dashboard/Services/Clients/Logs를 모두 보여주지 않는다.

1. **Calm shell**
   - 큰 hero marketing 문구 대신 sidebar identity와 compact page header를 사용한다.
   - admin token과 health 상태는 app chrome 안에 통합한다.

2. **Section card**
   - 주요 route section은 `cm-card`.
   - 내부 grouping은 `cm-panel` 또는 `cm-panel-subtle`.
   - shadow는 기본 사용하지 않는다.

3. **Kicker + title + copy**
   - `cm-kicker`: 작은 metadata label.
   - `cm-section-title`: `text-base/font-medium`.
   - `cm-copy`: `text-sm/muted-foreground`.

4. **Action hierarchy**
   - primary: dark neutral.
   - brand: brand tint, 제한적 사용.
   - secondary: neutral outline.
   - danger: destructive tint.

5. **Status visibility**
   - success/active: emerald.
   - running/info: blue.
   - warning/auth required: amber.
   - error/revoked/policy deny: rose.
   - neutral/unknown: muted/slate.

## 금지 패턴

- `dangerouslySetInnerHTML`로 icon/rendering 처리.
- inline SVG icon injection.
- admin/client token을 localStorage에 저장.
- decorative gradient/glow/shadow.
- `text-2xl+`, `font-semibold/bold` 중심의 marketing hierarchy.
- SaaS team/workspace/marketplace/billing UI 도입.

## 관련 문서

- [Code-level audit](./code-level-audit.md)
- [Component patterns](./component-patterns.md)
- [Theme token asset](./assets/coremcp-theme.tokens.json)
- [Palette SVG](./assets/coremcp-palette.svg)
- [Reusable CSS asset](./assets/coremcp-theme.css)
