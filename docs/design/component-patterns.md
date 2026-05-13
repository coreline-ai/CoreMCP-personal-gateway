# CoreMCP Component Patterns

## 1. Section

권장:

```tsx
<section className="cm-card">
  <p className="cm-kicker">Services</p>
  <h2 className="cm-section-title">MCP 추가/등록과 validation</h2>
  <p className="cm-copy">Remote MCP URL을 등록하고 DB catalog로 캐시합니다.</p>
</section>
```

사용 목적:

- route-level section
- service detail tab body
- major settings/log/playground block

## 2. Nested panel

```tsx
<div className="cm-panel-subtle">
  ...
</div>
```

사용 목적:

- form wrapper
- list item
- client guide card
- toolbox item

## 3. Buttons

| Variant | Class | 용도 |
|---|---|---|
| Primary | `cm-button cm-button-primary` | 실행/저장/호출 |
| Brand | `cm-button cm-button-brand` | API 상태 확인, validate 등 brand action |
| Secondary | `cm-button cm-button-secondary` | detail, refresh, navigation |
| Danger | `cm-button cm-button-danger` | revoke/delete/remove |

규칙:

- 같은 row에서 primary는 1개 이하.
- destructive action은 항상 rose tint.
- token/credential 관련 버튼은 문구를 구체적으로 쓴다.

## 4. Fields

```tsx
<input className="cm-input" />
<select className="cm-select" />
<textarea className="cm-textarea" />
```

규칙:

- admin token / downstream secret은 `type="password"` 유지.
- secret 평문은 response/log에 재표시하지 않는다.
- JSON textarea는 `font-mono`, dark code surface를 사용한다.

## 5. Badge / pill

Status badge는 `admin-utils.ts`의 mapping을 우선한다.

```tsx
<span className={classNames('cm-pill', statusPill(service.status))}>
  {service.status}
</span>
```

Semantic tone:

- success/active: emerald
- running/validating: blue
- warning/auth required: amber
- error/revoked/deny: rose
- unknown: slate

## 6. Code / JSON surface

```tsx
<pre className="cm-code-block">
  {JSON.stringify(payload, null, 2)}
</pre>
```

사용 목적:

- validation summary
- playground result
- schema JSON
- token prompt 1회 표시

## 7. Empty state

```tsx
<div className="cm-empty">
  <h3 className="font-medium text-foreground">도구함이 비어 있습니다.</h3>
  <p className="mt-1 text-sm text-muted-foreground">Fake MCP 추가 → Validate 실행 → 도구함 추가 순서로 시작하세요.</p>
</div>
```

규칙:

- 빈 상태는 다음 행동을 1개 이상 제시한다.
- 가짜 숫자/가짜 성공 상태를 만들지 않는다.

## 8. Icon

반드시 `ToolIcon` 또는 `<img>`만 사용한다.

금지:

```tsx
<div dangerouslySetInnerHTML={{ __html: svg }} />
```

권장:

```tsx
<ToolIcon tool={tool} />
```

## 9. Copy tone

- 한국어 본문 + 영문 기술 keyword 유지.
- 예: “도구함”, “연결된 AI client”, “MCP 추가/등록”, “policy deny”.
- “workspace”, “billing”, “marketplace”는 현재 UI copy에 사용하지 않는다.

## 10. Multica 흡수 규칙

- 제목도 `text-base font-medium`을 기본으로 한다.
- `font-semibold`, `font-bold`, `text-2xl+`는 새 UI에서 사용하지 않는다.
- hover는 `hover:bg-muted`, active는 `bg-muted text-foreground font-medium`로 구분한다.
- 카드/패널은 border + radius + spacing만 사용하고 shadow/gradient를 기본 금지한다.
- Tailwind hardcoded blue/gray는 새 primitive 내부가 아니라 semantic 상태에만 허용한다.
