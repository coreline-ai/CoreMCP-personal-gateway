'use client';

import { useMemo, useState } from 'react';
import type { AuditLogSummary, ToolInvocationSummary } from '@/lib/api';
import { classNames, logStatusPill, shortRequestId } from '../admin-utils';

interface LogsSectionProps {
  invocations: ToolInvocationSummary[];
  auditLogs: AuditLogSummary[];
}

type StatusFilter = 'all' | 'success' | 'errors' | 'policy_or_auth';

function matchesFilter(status: string | null | undefined, errorCode: string | null | undefined, action: string | undefined, filter: StatusFilter) {
  if (filter === 'all') return true;
  const normalizedStatus = status?.toLowerCase() ?? '';
  const normalizedError = errorCode?.toLowerCase() ?? '';
  const normalizedAction = action?.toLowerCase() ?? '';

  if (filter === 'success') return !errorCode && (normalizedStatus.includes('success') || normalizedStatus === 'ok' || normalizedStatus === 'completed');
  if (filter === 'policy_or_auth') return normalizedError.includes('policy') || normalizedError.includes('auth') || normalizedAction.includes('policy') || normalizedAction.includes('auth') || normalizedStatus.includes('deny');
  return Boolean(errorCode) || normalizedStatus.includes('error') || normalizedStatus.includes('failed') || normalizedStatus.includes('deny');
}

function InvocationCard({ item }: { item: ToolInvocationSummary }) {
  const toolLabel = item.exposed_tool_name ?? item.tool_name ?? item.method ?? 'unknown tool';
  const serviceLabel = item.service_name ?? item.service_slug ?? item.service_id ?? 'service unknown';

  return (
    <div className="cm-panel-subtle">
      <div className="flex flex-wrap items-center gap-2">
        <span className={classNames('rounded-full px-2.5 py-1 text-xs font-medium ring-1', logStatusPill(item.status, item.error_code))}>{item.status}</span>
        {item.error_code && <span className="rounded-full bg-rose-50 px-2.5 py-1 text-xs font-medium text-rose-700 ring-1 ring-rose-200">{item.error_code}</span>}
        <span className="rounded-full bg-card px-2.5 py-1 text-xs font-medium text-muted-foreground ring-1 ring-border">{item.latency_ms ?? 0}ms</span>
      </div>
      <p className="mt-3 font-mono text-sm text-foreground">{toolLabel}</p>
      <dl className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-2 lg:grid-cols-4">
        <div><dt className="font-medium text-muted-foreground">request_id</dt><dd className="mt-1 font-mono text-foreground">{shortRequestId(item.request_id)}</dd></div>
        <div><dt className="font-medium text-muted-foreground">service</dt><dd className="mt-1 font-mono text-foreground">{serviceLabel}</dd></div>
        <div><dt className="font-medium text-muted-foreground">method</dt><dd className="mt-1 font-mono text-foreground">{item.method ?? '—'}</dd></div>
        <div><dt className="font-medium text-muted-foreground">created</dt><dd className="mt-1 font-mono text-foreground">{item.created_at ?? '—'}</dd></div>
      </dl>
    </div>
  );
}

function AuditCard({ item }: { item: AuditLogSummary }) {
  const status = item.status ?? (item.error_code ? 'error' : 'recorded');
  const serviceLabel = item.service_name ?? item.service_slug ?? item.service_id ?? item.resource_id ?? 'resource unknown';
  const toolLabel = item.exposed_tool_name ?? item.tool_name ?? 'tool n/a';
  const clientLabel = item.client_name ?? item.client_type ?? 'client n/a';

  return (
    <div className="cm-panel-subtle">
      <div className="flex flex-wrap items-center gap-2">
        <span className={classNames('rounded-full px-2.5 py-1 text-xs font-medium ring-1', logStatusPill(status, item.error_code))}>{status}</span>
        {item.error_code && <span className="rounded-full bg-rose-50 px-2.5 py-1 text-xs font-medium text-rose-700 ring-1 ring-rose-200">{item.error_code}</span>}
        <span className="rounded-full bg-card px-2.5 py-1 text-xs font-medium text-muted-foreground ring-1 ring-border">{item.resource_type}</span>
      </div>
      <p className="mt-3 font-mono text-sm text-foreground">{item.action}</p>
      <dl className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-2 lg:grid-cols-5">
        <div><dt className="font-medium text-muted-foreground">request_id</dt><dd className="mt-1 font-mono text-foreground">{shortRequestId(item.request_id)}</dd></div>
        <div><dt className="font-medium text-muted-foreground">service/resource</dt><dd className="mt-1 font-mono text-foreground">{serviceLabel}</dd></div>
        <div><dt className="font-medium text-muted-foreground">tool</dt><dd className="mt-1 font-mono text-foreground">{toolLabel}</dd></div>
        <div><dt className="font-medium text-muted-foreground">client</dt><dd className="mt-1 font-mono text-foreground">{clientLabel}</dd></div>
        <div><dt className="font-medium text-muted-foreground">created</dt><dd className="mt-1 font-mono text-foreground">{item.created_at ?? '—'}</dd></div>
      </dl>
    </div>
  );
}

export function LogsSection({ invocations, auditLogs }: LogsSectionProps) {
  const [filter, setFilter] = useState<StatusFilter>('all');
  const filteredInvocations = useMemo(() => invocations.filter((item) => matchesFilter(item.status, item.error_code, item.method, filter)), [filter, invocations]);
  const filteredAuditLogs = useMemo(() => auditLogs.filter((item) => matchesFilter(item.status, item.error_code, item.action, filter)), [auditLogs, filter]);
  const errorCount = invocations.filter((item) => matchesFilter(item.status, item.error_code, item.method, 'errors')).length
    + auditLogs.filter((item) => matchesFilter(item.status, item.error_code, item.action, 'errors')).length;

  return (
    <section id="logs" className="cm-card">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <p className="cm-kicker">Logs</p>
          <h2 className="cm-section-title">최근 tool invocation / audit</h2>
          <p className="cm-copy max-w-3xl">
            request_id, service/tool, status, error_code를 한 카드에서 확인해 policy deny, auth failure, downstream error를 빠르게 구분합니다.
          </p>
        </div>
        <div className="cm-panel-subtle p-3">
          <label className="grid gap-2 text-xs font-medium text-muted-foreground">
            Status filter
            <select value={filter} onChange={(event) => setFilter(event.target.value as StatusFilter)} className="cm-select py-2">
              <option value="all">All events</option>
              <option value="success">Success only</option>
              <option value="errors">Errors / denied</option>
              <option value="policy_or_auth">Policy / auth</option>
            </select>
          </label>
          <p className="mt-2 text-xs text-muted-foreground">error-like events: {errorCount}</p>
        </div>
      </div>

      <h3 className="mt-6 text-base font-medium">Tool invocations</h3>
      <div className="mt-4 grid gap-3">
        {filteredInvocations.map((item) => <InvocationCard key={item.id} item={item} />)}
        {filteredInvocations.length === 0 && <p className="text-sm text-muted-foreground">선택한 조건의 호출 기록이 없습니다.</p>}
      </div>

      <h3 className="mt-6 text-base font-medium">Audit events</h3>
      <div className="mt-4 grid gap-3">
        {filteredAuditLogs.map((item) => <AuditCard key={item.id} item={item} />)}
        {filteredAuditLogs.length === 0 && <p className="text-sm text-muted-foreground">선택한 조건의 audit 기록이 없습니다.</p>}
      </div>
    </section>
  );
}
