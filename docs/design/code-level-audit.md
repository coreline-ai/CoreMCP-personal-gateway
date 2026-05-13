# CoreMCP Web Design Code-level Audit

분석 일시: `2026-05-13`
대상: `apps/web`
참조 원본: `/Users/hwanchoi/projects/multica/multica-main`

## 1. 재분석 결론

초기 디자인 primitive(`cm-*`)는 실행 화면에 로드되고 있었지만, 실제 Multica 디자인과는 다르게 보였다. 원인은 CSS 미적용이 아니라 **토큰과 위계가 CoreMCP 자체 blue/card 스타일에 머물러 있었기 때문**이다.

이번 수정으로 다음 차이를 제거했다.

| 이전 | 현재 |
|---|---|
| blue radial gradient background | neutral `--background` |
| 큰 hero title `text-3xl/sm:text-5xl` | `text-base/font-medium` |
| `font-semibold` 중심 hierarchy | `font-medium` 중심 |
| white/blue/shadow SaaS card | border + muted/card surface |
| pill nav with brand border | subtle muted active state |
| tool icon shadow/card emphasis | neutral border/card icon |

## 2. 현재 구조

| 영역 | 파일 | 역할 |
|---|---|---|
| Theme | `tailwind.config.ts` | CSS variable 기반 token mapping |
| Global style | `app/globals.css` | Multica OKLCh token, body, focus, primitive |
| Shell | `components/admin/admin-shell.tsx` | calm header, token form, subtle nav |
| Main dashboard | `components/admin/admin-console.tsx` | state orchestration, section composition |
| Route sections | `components/admin/sections/*.tsx` | Services/Toolbox/Clients/Settings/Playground/Logs |
| Service detail | `components/admin/service-detail-console.tsx` | 6-tab service operations |
| Icon | `components/tool-icon.tsx` | `<img>` only fallback policy |

## 3. 흡수한 Multica 코드 패턴

| Multica source | CoreMCP 반영 |
|---|---|
| `packages/ui/styles/tokens.css` | OKLCh `background/foreground/card/muted/border/brand` token |
| `packages/ui/components/ui/card.tsx` | `rounded-xl`, `bg-card`, ring/border 중심, no shadow |
| `packages/ui/components/ui/button.tsx` | `rounded-lg`, `text-sm`, `font-medium`, subtle hover |
| `docs/design.md` typography | `text-base/text-sm/text-xs`, `font-medium`, no `font-semibold` |
| `docs/design.md` interaction | hover muted, active muted+foreground, no scale/shadow |

## 4. 적용된 코드 변경

| 파일 | 변경 |
|---|---|
| `app/globals.css` | blue token 제거, OKLCh token + neutral primitive 재정의 |
| `tailwind.config.ts` | `background/foreground/card/muted/border/brand` token mapping 추가 |
| `admin-shell.tsx` | 큰 hero/horizontal nav 제거, Multica식 sidebar + 48px page header shell로 변경 |
| `admin-console.tsx` | 모든 section 동시 렌더링 제거, route별 active section만 렌더링 |
| `theme-toggle.tsx` | sidebar theme selector 추가, `coremcp_theme` localStorage 저장 |
| `app/layout.tsx` | 기본 dark class 적용 |
| `sections/dashboard-section.tsx` | metric 숫자 축소, shadow 제거 |
| `sections/services-section.tsx` | empty/list state를 neutral token으로 변경 |
| `sections/clients-section.tsx` | label/code/guide card를 neutral token으로 변경 |
| `sections/toolbox-section.tsx` | empty/list badge style 경량화 |
| `sections/playground-section.tsx` | form label/copy token화 |
| `sections/logs-section.tsx` | log metadata와 heading size 정리 |
| `service-detail-console.tsx` | tabs/header/tool control/detail sections 경량화 |
| `tool-icon.tsx` | icon shadow 제거 |

## 5. 보존해야 할 제품 제약

- CoreMCP는 개인용 gateway다. Multica의 team/workspace/marketplace/billing 패턴은 흡수하지 않는다.
- admin/client token은 sessionStorage/client-token flow만 유지한다.
- `<img>` 기반 tool icon policy를 유지한다.
- 외부 UI library를 추가하지 않는다.

## 6. 후속 권장

- 시간이 생기면 `Button`, `Card`, `Field`, `Badge` React primitive로 추출한다.
- 현재 즉시 적용을 위해 sidebar/header primitive는 `AdminShell` 안에 직접 구현했다. 후속 polish에서 `components/admin/layout/*`로 분리한다.
- dark mode는 기본값으로 적용되어 있다. Light/System 전환은 sidebar footer Theme selector에서 수행한다.
- 실제 모바일 기기 QA는 선택 polish로 둔다.
