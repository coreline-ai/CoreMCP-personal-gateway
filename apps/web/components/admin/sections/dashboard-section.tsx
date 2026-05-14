'use client';

import type { ClientTokenSummary, DashboardSummary, McpServiceSummary, SettingsResponse, ToolboxItemSummary, ToolboxSummary, ToolInvocationSummary } from '@/lib/api';

interface DashboardSectionProps {
  defaultToolbox: ToolboxSummary | null;
  toolboxItems: ToolboxItemSummary[];
  services: McpServiceSummary[];
  settings: SettingsResponse | null;
  clientTokens: ClientTokenSummary[];
  invocations: ToolInvocationSummary[];
  dashboardSummary: DashboardSummary | null;
}

export function DashboardSection({ defaultToolbox, toolboxItems, services, settings, clientTokens, invocations, dashboardSummary }: DashboardSectionProps) {
  const activeServices = services.filter((service) => service.status === 'active').length;
  const calls24h = dashboardSummary?.calls_24h;
  const healthFailing = dashboardSummary?.metrics?.mcp_services_health_failing ?? 0;
  const circuitOpen = dashboardSummary?.metrics?.mcp_services_circuit_open ?? 0;
  const errorRate = calls24h && calls24h.calls > 0 ? Math.round((calls24h.errors / calls24h.calls) * 100) : 0;
  const cards = [
    { title: 'Default Toolbox', value: `${defaultToolbox?.item_count ?? toolboxItems.length}`, body: defaultToolbox?.name ?? '기본 도구함을 불러오지 않았습니다.' },
    { title: 'MCP Services', value: `${activeServices}/${services.length}`, body: 'active / total service 수입니다.' },
    { title: 'Client Tokens', value: `${settings?.client_token_count ?? clientTokens.length}`, body: `auth mode: ${settings?.auth_mode ?? 'unknown'}` },
    { title: '24h Tool Calls', value: `${calls24h?.calls ?? invocations.length}`, body: `errors ${calls24h?.errors ?? 0} · avg ${calls24h?.avg_latency_ms ?? 0}ms` }
  ];

  return (
    <section id="dashboard" className="space-y-3">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {cards.map((card) => (
          <article key={card.title} className="cm-panel">
            <p className="text-xs font-medium text-muted-foreground">{card.title}</p>
            <p className="mt-2 text-base font-medium text-foreground">{card.value}</p>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">{card.body}</p>
          </article>
        ))}
      </div>

      <div className="grid gap-3 lg:grid-cols-3">
        <article className="cm-panel">
          <p className="text-xs font-medium text-muted-foreground">운영 상태</p>
          <p className="mt-2 text-sm text-foreground">error rate {errorRate}% · max latency {calls24h?.max_latency_ms ?? 0}ms</p>
          <p className="mt-2 text-sm text-muted-foreground">health failing {healthFailing} · circuit open {circuitOpen}</p>
          <p className="mt-2 text-xs text-muted-foreground">API version: {settings?.app_version ?? '—'}</p>
        </article>

        <article className="cm-panel lg:col-span-2">
          <p className="text-xs font-medium text-muted-foreground">Top tools · 24h</p>
          <div className="mt-3 space-y-2">
            {(dashboardSummary?.top_tools_24h ?? []).length === 0 ? (
              <p className="text-sm text-muted-foreground">최근 24시간 호출 기록이 없습니다.</p>
            ) : (
              dashboardSummary?.top_tools_24h.map((tool) => (
                <div key={tool.tool} className="flex items-center justify-between gap-3 rounded-xl border border-border bg-muted/20 px-3 py-2 text-sm">
                  <span className="truncate font-mono text-xs text-foreground">{tool.tool}</span>
                  <span className="shrink-0 text-muted-foreground">{tool.calls} calls · {tool.errors} errors · {tool.avg_latency_ms}ms avg</span>
                </div>
              ))
            )}
          </div>
        </article>
      </div>

      {(dashboardSummary?.unhealthy_services ?? []).length > 0 && (
        <article className="cm-panel">
          <p className="text-xs font-medium text-muted-foreground">Health probe attention</p>
          <div className="mt-3 grid gap-2">
            {dashboardSummary?.unhealthy_services.map((service) => (
              <div key={service.id} className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-foreground">
                {service.name} · failures {service.consecutive_failures} · last check {service.last_health_check_at ?? '—'}
              </div>
            ))}
          </div>
        </article>
      )}
    </section>
  );
}
