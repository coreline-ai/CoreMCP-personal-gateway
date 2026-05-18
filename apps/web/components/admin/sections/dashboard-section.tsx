'use client';

import Link from 'next/link';

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
    { title: 'Default Toolbox', value: `${defaultToolbox?.item_count ?? toolboxItems.length}`, body: defaultToolbox?.name ?? '기본 도구함을 불러오지 않았습니다.', href: '/toolbox', cta: '도구함 관리' },
    { title: 'MCP Services', value: `${activeServices}/${services.length}`, body: 'active / total service 수입니다.', href: '/services', cta: '서비스 보기' },
    { title: 'Client Tokens', value: `${settings?.client_token_count ?? clientTokens.length}`, body: `auth mode: ${settings?.auth_mode ?? 'unknown'}`, href: '/clients', cta: 'client 연결' },
    { title: '24h Tool Calls', value: `${calls24h?.calls ?? invocations.length}`, body: `errors ${calls24h?.errors ?? 0} · avg ${calls24h?.avg_latency_ms ?? 0}ms`, href: '/logs', cta: '로그 확인' }
  ];

  return (
    <section id="dashboard" className="space-y-3">
      <article className="cm-panel border-brand-200/70 bg-brand-50/60 dark:border-brand-400/20 dark:bg-brand-500/10">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-brand-700 dark:text-brand-200">Read-only overview</p>
            <p className="mt-2 text-sm leading-6 text-foreground">
              Dashboard는 운영 상태를 한눈에 보는 요약 화면입니다. 값 자체를 수정하는 화면은 아니며,
              아래 요약 카드를 누르면 관련 관리 화면으로 이동합니다.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link href="/services" className="cm-button cm-button-brand cm-button-sm">MCP 추가/등록</Link>
            <Link href="/playground" className="cm-button cm-button-secondary cm-button-sm">도구 테스트</Link>
            <Link href="/logs" className="cm-button cm-button-secondary cm-button-sm">최근 로그</Link>
          </div>
        </div>
      </article>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {cards.map((card) => (
          <Link key={card.title} href={card.href} className="cm-panel block transition hover:-translate-y-0.5 hover:border-brand-300 hover:shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-400/50">
            <p className="text-xs font-medium text-muted-foreground">{card.title}</p>
            <p className="mt-2 text-base font-medium text-foreground">{card.value}</p>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">{card.body}</p>
            <p className="mt-3 text-xs font-semibold text-brand-700 dark:text-brand-200">{card.cta} →</p>
          </Link>
        ))}
      </div>

      <div className="grid gap-3 lg:grid-cols-3">
        <article className="cm-panel">
          <p className="text-xs font-medium text-muted-foreground">운영 상태</p>
          <p className="mt-2 text-sm text-foreground">error rate {errorRate}% · max latency {calls24h?.max_latency_ms ?? 0}ms</p>
          <p className="mt-2 text-sm text-muted-foreground">health failing {healthFailing} · circuit open {circuitOpen}</p>
          <p className="mt-2 text-xs text-muted-foreground">API version: {settings?.app_version ?? '—'}</p>
          <p className="mt-3 text-xs text-muted-foreground">상세 원인은 Services의 validation 상태와 Logs의 호출 기록에서 확인합니다.</p>
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
