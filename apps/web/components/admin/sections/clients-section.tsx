'use client';

import type { FormEvent } from 'react';
import { getApiBaseUrl, type ExternalConnectionSummary } from '@/lib/api';

interface ClientsSectionProps {
  connections: ExternalConnectionSummary[];
  clientName: string;
  clientType: string;
  issuedToken: string | null;
  onClientNameChange: (value: string) => void;
  onClientTypeChange: (value: string) => void;
  onCreateConnection: (event: FormEvent<HTMLFormElement>) => void;
  onCreateOneTimeToken: () => void;
  onRevokeConnection: (connectionId: string) => void;
}

const guideCards = [
  {
    title: 'Codex CLI exec',
    badge: '추천',
    body: 'make codex-install로 Codex MCP config를 만들고 codex exec 실행 시 CoreMCP 도구함을 사용합니다.',
    action: 'client token은 ~/.coremcp/codex-client-token에 0600으로 저장되고 COREMCP_CLIENT_TOKEN 환경변수로만 주입됩니다.'
  },
  {
    title: 'Claude Code / bearer',
    badge: 'optional',
    body: 'Claude Code 호환성은 유지하지만, 현재 1차 운영 경로는 Codex CLI exec입니다.',
    action: '필요할 때만 별도 client token을 발급해 Authorization bearer로 등록하세요.'
  },
  {
    title: 'OAuth client',
    badge: 'optional',
    body: 'AUTH_MODE=oauth 환경에서 discovery/authorization flow를 지원하는 client에 사용합니다.',
    action: '기본값은 static_bearer입니다. OAuth가 꺼져 있으면 등록+Token 방식을 유지하세요.'
  },
  {
    title: 'OpenClaw / one-time token',
    badge: '1회 연결',
    body: 'OAuth 없이 연결 prompt/code가 필요한 client는 One-time Token 버튼으로 짧게 만료되는 token을 발급합니다.',
    action: '발급된 prompt와 token을 client에 붙여 넣고, 만료되면 새로 발급합니다.'
  }
];

export function ClientsSection({
  connections,
  clientName,
  clientType,
  issuedToken,
  onClientNameChange,
  onClientTypeChange,
  onCreateConnection,
  onCreateOneTimeToken,
  onRevokeConnection
}: ClientsSectionProps) {
  const mcpUrl = `${getApiBaseUrl().replace(/\/$/, '')}/mcp`;

  return (
    <article id="clients" className="cm-card">
      <p className="cm-kicker">Connected Clients</p>
      <h2 className="cm-section-title">연결된 AI client</h2>
      <p className="cm-copy">Codex CLI exec를 1차 경로로 사용하고, Claude Code/OAuth/OpenClaw는 선택 client로 연결합니다.</p>

      <form onSubmit={onCreateConnection} className="mt-4 grid gap-3 cm-panel-subtle lg:grid-cols-[1fr_1fr_auto_auto]">
        <label className="grid gap-1 text-xs font-medium text-muted-foreground">
          Client name
          <input value={clientName} onChange={(event) => onClientNameChange(event.target.value)} className="cm-input py-2" />
        </label>
        <label className="grid gap-1 text-xs font-medium text-muted-foreground">
          Client type
          <select value={clientType} onChange={(event) => onClientTypeChange(event.target.value)} className="cm-select py-2">
            <option value="codex_cli">codex_cli</option>
            <option value="claude_code">claude_code</option>
            <option value="openclaw">openclaw</option>
            <option value="cursor">cursor</option>
            <option value="chatgpt">chatgpt</option>
            <option value="oauth_client">oauth_client</option>
            <option value="other">other</option>
          </select>
        </label>
        <button type="submit" className="self-end cm-button cm-button-primary">등록+Token</button>
        <button type="button" onClick={onCreateOneTimeToken} className="self-end cm-button cm-button-brand">
          One-time Token
        </button>
      </form>

      <div className="mt-4 cm-panel text-sm leading-6 text-muted-foreground">
        <p className="font-medium text-foreground">MCP endpoint</p>
        <p className="mt-1 font-mono text-xs text-muted-foreground">{mcpUrl}</p>
        <p className="mt-2">Codex CLI는 아래 helper가 client token을 발급하고 MCP server를 등록합니다.</p>
        <pre className="mt-3 whitespace-pre-wrap rounded-lg bg-primary px-3 py-3 font-mono text-xs leading-5 text-primary-foreground">{`make codex-install
infra/scripts/codex-exec-coremcp.sh "CoreMCP 도구 목록을 확인해줘"`}</pre>
        <p className="mt-3">Bearer 지원 client는 <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">Authorization: Bearer cmcp_client_…</code> 헤더를 사용할 수 있습니다.</p>
      </div>

      <div className="mt-4 grid gap-3 lg:grid-cols-2 xl:grid-cols-4">
        {guideCards.map((card) => (
          <div key={card.title} className="cm-panel-subtle">
            <div className="flex items-center justify-between gap-3">
              <h3 className="font-medium text-foreground">{card.title}</h3>
              <span className="rounded-full bg-card px-2.5 py-1 text-xs font-medium text-muted-foreground ring-1 ring-border">{card.badge}</span>
            </div>
            <p className="mt-3 text-sm leading-6 text-muted-foreground">{card.body}</p>
            <p className="mt-3 rounded-lg bg-card px-3 py-2 text-xs leading-5 text-muted-foreground ring-1 ring-border">{card.action}</p>
          </div>
        ))}
      </div>

      {issuedToken && (
        <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950">
          <strong>1회 표시 token / prompt:</strong>
          <pre className="mt-2 whitespace-pre-wrap break-words font-mono text-xs">{issuedToken}</pre>
        </div>
      )}

      <div className="mt-6 grid gap-3">
        {connections.map((client) => (
          <div key={client.id} className="cm-panel-subtle flex items-center justify-between gap-3 px-4 py-3">
            <div>
              <p className="font-medium text-foreground">{client.client_name}</p>
              <p className="mt-1 text-xs text-muted-foreground">{client.client_type} · {client.status} · last {client.last_used_at ?? 'never'}</p>
            </div>
            {client.status !== 'revoked' && (
              <button type="button" onClick={() => onRevokeConnection(client.id)} className="cm-button cm-button-danger cm-button-sm">
                Revoke
              </button>
            )}
          </div>
        ))}
        {connections.length === 0 && <p className="text-sm text-muted-foreground">아직 연결된 AI client가 없습니다. Codex CLI라면 make codex-install 또는 위 등록+Token부터 시작하세요.</p>}
      </div>
    </article>
  );
}
