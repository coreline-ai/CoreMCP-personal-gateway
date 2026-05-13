'use client';

import type { ClientTokenSummary, McpServiceSummary, SettingsResponse, ToolboxItemSummary, ToolboxSummary, ToolInvocationSummary } from '@/lib/api';

interface DashboardSectionProps {
  defaultToolbox: ToolboxSummary | null;
  toolboxItems: ToolboxItemSummary[];
  services: McpServiceSummary[];
  settings: SettingsResponse | null;
  clientTokens: ClientTokenSummary[];
  invocations: ToolInvocationSummary[];
}

export function DashboardSection({ defaultToolbox, toolboxItems, services, settings, clientTokens, invocations }: DashboardSectionProps) {
  const activeServices = services.filter((service) => service.status === 'active').length;
  const cards = [
    { title: 'Default Toolbox', value: `${defaultToolbox?.item_count ?? toolboxItems.length}`, body: defaultToolbox?.name ?? '기본 도구함을 불러오지 않았습니다.' },
    { title: 'MCP Services', value: `${activeServices}/${services.length}`, body: 'active / total service 수입니다.' },
    { title: 'Client Tokens', value: `${settings?.client_token_count ?? clientTokens.length}`, body: `auth mode: ${settings?.auth_mode ?? 'unknown'}` },
    { title: 'Recent Tool Calls', value: `${invocations.length}`, body: `API version: ${settings?.app_version ?? '—'}` }
  ];

  return (
    <section id="dashboard" className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
      {cards.map((card) => (
        <article key={card.title} className="cm-panel">
          <p className="text-xs font-medium text-muted-foreground">{card.title}</p>
          <p className="mt-2 text-base font-medium text-foreground">{card.value}</p>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">{card.body}</p>
        </article>
      ))}
    </section>
  );
}
