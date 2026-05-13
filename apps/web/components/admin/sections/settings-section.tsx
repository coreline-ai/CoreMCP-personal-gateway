'use client';

import type { ClientTokenSummary } from '@/lib/api';

interface SettingsSectionProps {
  tokenPreview: string;
  clientTokens: ClientTokenSummary[];
  onRevokeClientToken: (tokenId: string) => void;
}

export function SettingsSection({ tokenPreview, clientTokens, onRevokeClientToken }: SettingsSectionProps) {
  return (
    <article id="settings" className="cm-card">
      <p className="cm-kicker">Settings / Tokens</p>
      <h2 className="cm-section-title">Token 관리</h2>
      <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-950">
        <strong>주의:</strong> admin token은 브라우저 sessionStorage에만 저장됩니다. client token 평문은 발급 응답에서 1회만 표시됩니다.
      </div>
      <div className="mt-4 grid gap-3">
        <div className="cm-panel"><p className="text-sm font-medium text-muted-foreground">Admin Token</p><p className="mt-2 font-mono text-sm text-foreground">{tokenPreview}</p></div>
        {clientTokens.map((item) => (
          <div key={item.id} className="cm-panel flex items-center justify-between gap-3">
            <div>
              <p className="font-mono text-sm text-foreground">{item.token_prefix}</p>
              <p className="mt-1 text-xs text-muted-foreground">{item.status} · {item.external_connection_id}</p>
            </div>
            {item.status !== 'revoked' && (
              <button type="button" onClick={() => onRevokeClientToken(item.id)} className="cm-button cm-button-danger cm-button-sm">
                Revoke
              </button>
            )}
          </div>
        ))}
      </div>
    </article>
  );
}
