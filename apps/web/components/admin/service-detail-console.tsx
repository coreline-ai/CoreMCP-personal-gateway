'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { ToolIcon } from '@/components/tool-icon';
import {
  coreMcpApi,
  clearAdminToken,
  getStoredAdminToken,
  saveAdminToken,
  type AuditLogSummary,
  type McpServiceSummary,
  type ServiceCredentialSummary,
  type ServiceToolSummary,
  type ToolPreset,
  type ToolInvocationSummary,
  type ToolOverrideSummary,
  type ToolPermissionLevel
} from '@/lib/api';
import { AdminShell } from './admin-shell';
import { classNames, explainError, logStatusPill, maskToken, riskPill, shortRequestId, statusPill, validationStages } from './admin-utils';

const detailTabs = ['Overview', 'Tools', 'Validation', 'Credential', 'Logs', 'Settings'] as const;
type DetailTab = (typeof detailTabs)[number];
const HEALTH_CHECK_PROMPT = 'Health 버튼을 눌러 API 상태를 확인하세요.';

interface ServiceDetailConsoleProps {
  serviceId: string;
}

const permissionOptions: Array<{ value: ToolPermissionLevel; label: string; description: string }> = [
  { value: 'callable', label: 'Callable', description: 'tools/list에 노출되고 tools/call이 허용됩니다.' },
  { value: 'visible_only', label: 'Visible only', description: '목록에는 보이지만 호출은 policy deny 됩니다.' },
  { value: 'hidden', label: 'Hidden', description: '외부 AI client의 tools/list에서 숨깁니다.' }
];

const toolPresetOptions: Array<{ value: ToolPreset; label: string; description: string }> = [
  { value: 'readonly', label: 'Read-only', description: 'readOnlyHint tool만 호출 가능하게 남기고 나머지는 숨깁니다.' },
  { value: 'dangerous_off', label: 'Dangerous off', description: 'destructive/high-risk tool만 숨기고 일반 tool은 호출 가능하게 둡니다.' },
  { value: 'full_access', label: 'Full access', description: '현재 service의 모든 active tool을 callable로 전환합니다.' }
];

type MetadataDraft = {
  category: string;
  homepage_url: string;
  documentation_url: string;
  logo_url: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function textFromUnknown(value: unknown): string | null {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return null;
}

function summaryValue(summary: Record<string, unknown> | null | undefined, keys: string[]): string | null {
  if (!summary) return null;
  for (const key of keys) {
    const direct = textFromUnknown(summary[key]);
    if (direct) return direct;
  }
  return null;
}

function summaryCount(summary: Record<string, unknown> | null | undefined, keys: string[]): number | null {
  if (!summary) return null;
  for (const key of keys) {
    const value = summary[key];
    if (typeof value === 'number') return value;
    if (Array.isArray(value)) return value.length;
  }
  return null;
}

function warningList(summary: Record<string, unknown> | null | undefined): string[] {
  if (!summary) return [];
  const warnings = summary.warnings ?? summary.validation_warnings ?? summary.warning_messages;
  if (!Array.isArray(warnings)) return [];
  return warnings.map((warning) => {
    const primitive = textFromUnknown(warning);
    if (primitive) return primitive;
    if (isRecord(warning)) return textFromUnknown(warning.message) ?? JSON.stringify(warning);
    return String(warning);
  });
}

function recordList(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) return [];
  return value.filter(isRecord);
}

function schemaDiffDetails(summary: Record<string, unknown> | null | undefined) {
  const schemaDiff = summary?.schema_diff;
  if (!isRecord(schemaDiff)) return { added: [], removed: [], changed: [] };
  return {
    added: recordList(schemaDiff.added),
    removed: recordList(schemaDiff.removed),
    changed: recordList(schemaDiff.changed)
  };
}

function findOverrideForTool(tool: ServiceToolSummary, overrides: ToolOverrideSummary[]) {
  return overrides.find((override) => (
    override.service_tool_id === tool.id
    || override.exposed_name === tool.exposed_name
    || override.exposed_name === tool.original_name
  ));
}

function serviceMatchesInvocation(invocation: ToolInvocationSummary, service: McpServiceSummary, fallbackId: string) {
  if (invocation.service_id && invocation.service_id === service.id) return true;
  if (service.slug && invocation.service_slug === service.slug) return true;
  const exposedName = invocation.exposed_tool_name ?? invocation.tool_name ?? '';
  return Boolean(service.slug && exposedName.startsWith(`${service.slug}.`)) || invocation.service_id === fallbackId;
}

function serviceMatchesAudit(audit: AuditLogSummary, service: McpServiceSummary, fallbackId: string) {
  if (audit.service_id && audit.service_id === service.id) return true;
  if (service.slug && audit.service_slug === service.slug) return true;
  if (audit.resource_id === service.id || audit.resource_id === fallbackId) return true;
  const exposedName = audit.exposed_tool_name ?? audit.tool_name ?? '';
  return Boolean(service.slug && exposedName.startsWith(`${service.slug}.`));
}

function ToolControlBadge({ enabled, permissionLevel }: { enabled: boolean; permissionLevel: ToolPermissionLevel }) {
  const tone = !enabled || permissionLevel === 'hidden'
    ? 'bg-slate-100 text-slate-700 ring-slate-200'
    : permissionLevel === 'visible_only'
      ? 'bg-amber-50 text-amber-700 ring-amber-200'
      : 'bg-emerald-50 text-emerald-700 ring-emerald-200';
  const label = !enabled ? 'disabled' : permissionLevel;
  return <span className={classNames('rounded-full px-2.5 py-1 text-xs font-medium ring-1', tone)}>{label}</span>;
}

function MetadataCard({ label, value, tone = 'default' }: { label: string; value: string; tone?: 'default' | 'warning' | 'danger' | 'success' }) {
  const toneClass = tone === 'warning'
    ? 'border-amber-200 bg-amber-50 text-amber-950'
    : tone === 'danger'
      ? 'border-rose-200 bg-rose-50 text-rose-950'
      : tone === 'success'
        ? 'border-emerald-200 bg-emerald-50 text-emerald-950'
        : 'border-border bg-card text-foreground';

  return (
    <div className={classNames('rounded-lg border p-4', toneClass)}>
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="mt-2 break-words font-mono text-sm">{value}</dd>
    </div>
  );
}

function InvocationLogCard({ item }: { item: ToolInvocationSummary }) {
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
      <dl className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
        <div><dt className="font-medium text-muted-foreground">request_id</dt><dd className="mt-1 font-mono text-foreground">{shortRequestId(item.request_id)}</dd></div>
        <div><dt className="font-medium text-muted-foreground">service</dt><dd className="mt-1 font-mono text-foreground">{serviceLabel}</dd></div>
        <div><dt className="font-medium text-muted-foreground">method</dt><dd className="mt-1 font-mono text-foreground">{item.method ?? '—'}</dd></div>
        <div><dt className="font-medium text-muted-foreground">created</dt><dd className="mt-1 font-mono text-foreground">{item.created_at ?? '—'}</dd></div>
      </dl>
    </div>
  );
}

function AuditLogCard({ item }: { item: AuditLogSummary }) {
  const status = item.status ?? (item.error_code ? 'error' : 'recorded');
  const serviceLabel = item.service_name ?? item.service_slug ?? item.service_id ?? item.resource_id ?? 'resource unknown';
  const toolLabel = item.exposed_tool_name ?? item.tool_name ?? 'tool n/a';

  return (
    <div className="cm-panel-subtle">
      <div className="flex flex-wrap items-center gap-2">
        <span className={classNames('rounded-full px-2.5 py-1 text-xs font-medium ring-1', logStatusPill(status, item.error_code))}>{status}</span>
        {item.error_code && <span className="rounded-full bg-rose-50 px-2.5 py-1 text-xs font-medium text-rose-700 ring-1 ring-rose-200">{item.error_code}</span>}
        <span className="rounded-full bg-card px-2.5 py-1 text-xs font-medium text-muted-foreground ring-1 ring-border">{item.resource_type}</span>
      </div>
      <p className="mt-3 font-mono text-sm text-foreground">{item.action}</p>
      <dl className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
        <div><dt className="font-medium text-muted-foreground">request_id</dt><dd className="mt-1 font-mono text-foreground">{shortRequestId(item.request_id)}</dd></div>
        <div><dt className="font-medium text-muted-foreground">service/resource</dt><dd className="mt-1 font-mono text-foreground">{serviceLabel}</dd></div>
        <div><dt className="font-medium text-muted-foreground">tool</dt><dd className="mt-1 font-mono text-foreground">{toolLabel}</dd></div>
        <div><dt className="font-medium text-muted-foreground">created</dt><dd className="mt-1 font-mono text-foreground">{item.created_at ?? '—'}</dd></div>
      </dl>
    </div>
  );
}

export function ServiceDetailConsole({ serviceId }: ServiceDetailConsoleProps) {
  const [token, setToken] = useState<string | null>(null);
  const [tokenInput, setTokenInput] = useState('');
  const [mounted, setMounted] = useState(false);
  const [healthMessage, setHealthMessage] = useState(HEALTH_CHECK_PROMPT);
  const [statusMessage, setStatusMessage] = useState('Admin token 저장 후 service detail을 불러오세요.');
  const [service, setService] = useState<McpServiceSummary | null>(null);
  const [tools, setTools] = useState<ServiceToolSummary[]>([]);
  const [toolOverrides, setToolOverrides] = useState<ToolOverrideSummary[]>([]);
  const [credential, setCredential] = useState<ServiceCredentialSummary | null>(null);
  const [invocations, setInvocations] = useState<ToolInvocationSummary[]>([]);
  const [auditLogs, setAuditLogs] = useState<AuditLogSummary[]>([]);
  const [activeTab, setActiveTab] = useState<DetailTab>('Overview');
  const [credentialType, setCredentialType] = useState('bearer_token');
  const [headerName, setHeaderName] = useState('Authorization');
  const [secretInput, setSecretInput] = useState('');
  const [deleteConfirm, setDeleteConfirm] = useState('');
  const [updatingToolId, setUpdatingToolId] = useState<string | null>(null);
  const [applyingPreset, setApplyingPreset] = useState<ToolPreset | null>(null);
  const [isValidating, setIsValidating] = useState(false);
  const [metadataDraft, setMetadataDraft] = useState<MetadataDraft>({
    category: '',
    homepage_url: '',
    documentation_url: '',
    logo_url: ''
  });

  useEffect(() => {
    const stored = getStoredAdminToken();
    setToken(stored);
    setTokenInput(stored ?? '');
    setMounted(true);

    const handleUnauthorized = () => {
      setToken(null);
      setTokenInput('');
      setHealthMessage('토큰이 만료되었거나 회전되었습니다. 새 토큰을 입력해 주세요.');
      setStatusMessage('인증 실패로 sessionStorage token을 삭제했습니다.');
    };

    window.addEventListener('coremcp:unauthorized', handleUnauthorized);
    return () => window.removeEventListener('coremcp:unauthorized', handleUnauthorized);
  }, []);

  useEffect(() => {
    if (token) {
      void refreshDetail();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, serviceId]);

  const tokenPreview = useMemo(() => maskToken(token), [token]);
  const serviceName = service?.name ?? `Service ${serviceId}`;
  const canDelete = deleteConfirm === (service?.slug ?? service?.id ?? '');
  const validationSummary = service?.validation_summary ?? null;
  const validationWarnings = warningList(validationSummary);
  const warningCount = validationWarnings.length || summaryCount(validationSummary, ['warning_count', 'warnings_count']) || tools.reduce((total, tool) => total + (tool.warning_count ?? tool.warnings?.length ?? 0), 0);
  const schemaHash = summaryValue(validationSummary, ['schema_hash', 'catalog_schema_hash', 'tools_schema_hash']) ?? (tools.length === 1 ? tools[0]?.schema_hash ?? null : null);
  const validationState = summaryValue(validationSummary, ['status', 'validation_status', 'last_status']) ?? service?.status ?? 'unknown';
  const riskLevel = service?.risk_level ?? summaryValue(validationSummary, ['risk_level', 'max_risk_level']) ?? 'unknown';
  const schemaDiff = useMemo(() => schemaDiffDetails(validationSummary), [validationSummary]);

  const toolControlSummary = useMemo(() => {
    return tools.reduce(
      (summary, tool) => {
        const override = findOverrideForTool(tool, toolOverrides);
        const enabled = override?.enabled ?? true;
        const permissionLevel = override?.permission_level ?? 'callable';
        if (!enabled) summary.disabled += 1;
        else if (permissionLevel === 'hidden') summary.hidden += 1;
        else if (permissionLevel === 'visible_only') summary.visibleOnly += 1;
        else summary.callable += 1;
        return summary;
      },
      { callable: 0, visibleOnly: 0, hidden: 0, disabled: 0 }
    );
  }, [tools, toolOverrides]);

  async function loadServiceWithFallback() {
    try {
      return await coreMcpApi.getService(serviceId);
    } catch (error) {
      const list = await coreMcpApi.listServices();
      const fallback = list.items.find((item) => item.id === serviceId || item.slug === serviceId);
      if (fallback) return fallback;
      throw error;
    }
  }

  async function refreshDetail() {
    if (!getStoredAdminToken()) return;
    setStatusMessage('Service detail을 불러오는 중입니다...');
    try {
      const serviceResponse = await loadServiceWithFallback();
      const resolvedServiceId = serviceResponse.id ?? serviceId;
      const [toolsResponse, credentialResponse, overridesResponse, invocationsResponse, auditResponse] = await Promise.all([
        coreMcpApi.listServiceTools(resolvedServiceId).catch(() => ({ items: [], next_cursor: null })),
        coreMcpApi.getServiceCredential(resolvedServiceId).catch(() => null),
        coreMcpApi.listToolOverrides(resolvedServiceId).catch(() => ({ items: [], next_cursor: null })),
        coreMcpApi.listToolInvocations(),
        coreMcpApi.listAuditLogs()
      ]);
      setService(serviceResponse);
      setMetadataDraft({
        category: serviceResponse.category ?? '',
        homepage_url: serviceResponse.homepage_url ?? '',
        documentation_url: serviceResponse.documentation_url ?? '',
        logo_url: serviceResponse.logo_url ?? ''
      });
      setTools(toolsResponse.items);
      setToolOverrides(overridesResponse.items);
      setCredential(credentialResponse);
      setInvocations(invocationsResponse.items.filter((item) => serviceMatchesInvocation(item, serviceResponse, serviceId)));
      setAuditLogs(auditResponse.items.filter((item) => serviceMatchesAudit(item, serviceResponse, serviceId)));
      setStatusMessage('Service detail을 최신 상태로 불러왔습니다.');
    } catch (error) {
      setStatusMessage(explainError(error));
    }
  }

  function handleTokenSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextToken = tokenInput.trim();
    if (!nextToken) return;
    saveAdminToken(nextToken);
    setToken(nextToken);
    void runHealthCheck('admin token을 저장했습니다. API 상태를 확인하는 중입니다...');
  }

  function handleTokenClear() {
    clearAdminToken();
    setToken(null);
    setTokenInput('');
    setService(null);
    setTools([]);
    setToolOverrides([]);
    setCredential(null);
    setInvocations([]);
    setAuditLogs([]);
    setHealthMessage('sessionStorage에 저장된 admin token을 삭제했습니다.');
  }

  async function runHealthCheck(loadingMessage = 'API 상태를 확인하는 중입니다...') {
    setHealthMessage(loadingMessage);
    try {
      const health = await coreMcpApi.health();
      setHealthMessage(`API 상태: ${health.status}`);
    } catch (error) {
      setHealthMessage(explainError(error));
    }
  }

  async function handleHealthCheck() {
    await runHealthCheck();
  }

  async function handleValidate() {
    if (isValidating) return;
    setIsValidating(true);
    setStatusMessage('Validation을 실행하는 중입니다...');
    try {
      const report = await coreMcpApi.validateService(service?.id ?? serviceId);
      await refreshDetail();
      setStatusMessage(`Validation ${report.status}: tools ${report.tools_found}개`);
      setActiveTab('Validation');
    } catch (error) {
      setStatusMessage(explainError(error));
      await refreshDetail();
    } finally {
      setIsValidating(false);
    }
  }

  async function handleToolControlChange(tool: ServiceToolSummary, patch: Partial<{ enabled: boolean; permission_level: ToolPermissionLevel }>) {
    const current = findOverrideForTool(tool, toolOverrides);
    const body = {
      enabled: patch.enabled ?? current?.enabled ?? true,
      permission_level: patch.permission_level ?? current?.permission_level ?? 'callable'
    };
    const targetServiceId = service?.id ?? tool.service_id ?? serviceId;

    setUpdatingToolId(tool.id);
    setStatusMessage(`${tool.exposed_name ?? tool.original_name} tool policy를 저장하는 중입니다...`);
    try {
      const updated = await coreMcpApi.updateToolOverride(targetServiceId, tool.id, body);
      setToolOverrides((previous) => {
        const others = previous.filter((override) => override.service_tool_id !== updated.service_tool_id);
        return [...others, updated];
      });
      setStatusMessage(`${updated.exposed_name} tool policy를 저장했습니다.`);
    } catch (error) {
      setStatusMessage(explainError(error));
    } finally {
      setUpdatingToolId(null);
    }
  }

  async function handleApplyPreset(preset: ToolPreset) {
    if (preset === 'full_access' && !window.confirm('Full access는 destructive tool까지 호출 가능하게 만들 수 있습니다. 계속할까요?')) return;
    const targetServiceId = service?.id ?? serviceId;
    setApplyingPreset(preset);
    setStatusMessage(`${preset} tool preset을 적용하는 중입니다...`);
    try {
      const response = await coreMcpApi.applyToolPreset(targetServiceId, preset);
      setToolOverrides(response.items);
      setStatusMessage(
        `${preset} preset 적용 완료: callable ${response.counts.callable ?? 0}, hidden ${response.counts.hidden ?? 0}, visible ${response.counts.visible_only ?? 0}`
      );
    } catch (error) {
      setStatusMessage(explainError(error));
    } finally {
      setApplyingPreset(null);
    }
  }

  async function handleCredentialSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!secretInput.trim()) return;
    setStatusMessage('Credential을 vault에 저장하는 중입니다...');
    try {
      const payload = { credential_type: credentialType, secret: secretInput.trim(), header_name: headerName.trim() || undefined };
      const response = credential?.status && credential.status !== 'not_connected'
        ? await coreMcpApi.rotateServiceCredential(service?.id ?? serviceId, payload)
        : await coreMcpApi.putServiceCredential(service?.id ?? serviceId, payload);
      setCredential(response);
      setSecretInput('');
      setStatusMessage('Credential을 저장했습니다. 평문은 UI state에서 제거했습니다.');
      await refreshDetail();
    } catch (error) {
      setStatusMessage(explainError(error));
    }
  }

  async function handleDeleteCredential() {
    if (!window.confirm('Downstream credential을 삭제할까요? 이 service는 auth_required 상태가 될 수 있습니다.')) return;
    setStatusMessage('Credential을 삭제하는 중입니다...');
    try {
      await coreMcpApi.deleteServiceCredential(service?.id ?? serviceId);
      setCredential({ status: 'not_connected', masked: null, updated_at: null });
      setStatusMessage('Credential을 vault에서 삭제하고 service를 auth_required 상태로 전환했습니다.');
      await refreshDetail();
    } catch (error) {
      setStatusMessage(explainError(error));
    }
  }

  async function handleDeleteService() {
    if (!canDelete) return;
    setStatusMessage('Service를 삭제하는 중입니다...');
    try {
      await coreMcpApi.deleteService(service?.id ?? serviceId);
      setStatusMessage('Service를 soft-delete했습니다. /services에서 목록을 새로고침하세요.');
      await refreshDetail();
    } catch (error) {
      setStatusMessage(explainError(error));
    }
  }

  async function handleMetadataSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const targetServiceId = service?.id ?? serviceId;
    setStatusMessage('Service metadata를 저장하는 중입니다...');
    try {
      const updated = await coreMcpApi.updateService(targetServiceId, {
        category: metadataDraft.category.trim() || null,
        homepage_url: metadataDraft.homepage_url.trim() || null,
        documentation_url: metadataDraft.documentation_url.trim() || null,
        logo_url: metadataDraft.logo_url.trim() || null
      });
      setService(updated);
      setMetadataDraft({
        category: updated.category ?? '',
        homepage_url: updated.homepage_url ?? '',
        documentation_url: updated.documentation_url ?? '',
        logo_url: updated.logo_url ?? ''
      });
      setStatusMessage('Service metadata를 저장했습니다.');
    } catch (error) {
      setStatusMessage(explainError(error));
    }
  }

  return (
    <AdminShell
      activeSection="services"
      statusMessage={statusMessage}
      token={token}
      tokenInput={tokenInput}
      tokenPreview={tokenPreview}
      healthMessage={healthMessage}
      mounted={mounted}
      onTokenInputChange={setTokenInput}
      onTokenSubmit={handleTokenSubmit}
      onTokenClear={handleTokenClear}
      onHealthCheck={handleHealthCheck}
      onRefresh={refreshDetail}
    >
      <section className="cm-card">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <Link href="/services" className="text-sm font-medium text-brand-700 transition hover:underline">← Services</Link>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <h2 className="text-base font-medium text-foreground">{serviceName}</h2>
              <span className={classNames('rounded-full px-3 py-1 text-xs font-medium ring-1', statusPill(service?.status))}>{service?.status ?? 'fallback'}</span>
              <span className={classNames('rounded-full px-3 py-1 text-xs font-medium ring-1', riskPill(riskLevel))}>{riskLevel} risk</span>
            </div>
            <p className="mt-2 font-mono text-sm text-muted-foreground">{service?.slug ?? serviceId} · {service?.endpoint_url ?? 'endpoint unknown'}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={handleValidate} disabled={isValidating} className="cm-button cm-button-brand disabled:cursor-not-allowed disabled:opacity-60">{isValidating ? '검증 중...' : 'Validate'}</button>
            <button type="button" onClick={() => setActiveTab('Credential')} className="cm-button cm-button-secondary">Credential</button>
          </div>
        </div>

        <div className="mt-4 flex gap-1 overflow-x-auto pb-1" role="tablist" aria-label="Service detail tabs">
          {detailTabs.map((tab) => (
            <button
              key={tab}
              type="button"
              role="tab"
              aria-selected={activeTab === tab}
              onClick={() => setActiveTab(tab)}
              className={classNames('whitespace-nowrap rounded-lg px-2.5 py-1.5 text-sm transition-colors hover:bg-muted hover:text-foreground', activeTab === tab ? 'bg-muted font-medium text-foreground' : 'text-muted-foreground')}
            >
              {tab}
            </button>
          ))}
        </div>
      </section>

      {activeTab === 'Overview' && (
        <section className="grid gap-4 xl:grid-cols-[0.8fr_1.2fr]">
          <article className="cm-card">
            <p className="cm-kicker">Overview</p>
            <h3 className="cm-section-title">운영 상태</h3>
            <dl className="mt-5 grid gap-3">
              {[
                ['Service ID', service?.id ?? serviceId],
                ['Status', service?.status ?? 'unknown'],
                ['Auth type', service?.auth_type ?? 'none'],
                ['Category', service?.category ?? 'uncategorized'],
                ['Credential', credential?.status ?? service?.credential_status ?? 'unknown'],
                ['Tools', `${service?.tool_count ?? tools.length}`],
                ['Last validated', service?.last_validated_at ?? 'never']
              ].map(([label, value]) => <MetadataCard key={label} label={label} value={value} />)}
            </dl>
          </article>
          <article className="cm-card">
            <p className="cm-kicker">Validation summary</p>
            <h3 className="cm-section-title">최근 검증 결과</h3>
            <p className="cm-copy">URL safety, initialize, tools/list, metadata scan 결과를 운영 판단에 사용합니다.</p>
            <dl className="mt-5 grid gap-3 sm:grid-cols-2">
              <MetadataCard label="Validation state" value={validationState} tone={validationState === 'error' ? 'danger' : 'default'} />
              <MetadataCard label="Risk level" value={riskLevel} tone={riskLevel === 'high' || riskLevel === 'dangerous' ? 'danger' : 'default'} />
              <MetadataCard label="Schema hash" value={schemaHash ?? 'API 제공 없음'} />
              <MetadataCard label="Warnings" value={`${warningCount}`} tone={warningCount > 0 ? 'warning' : 'success'} />
            </dl>
            <pre className="mt-5 cm-code-block max-h-96">{JSON.stringify(service?.validation_summary ?? { message: '검증 요약이 아직 없습니다.' }, null, 2)}</pre>
          </article>
        </section>
      )}

      {activeTab === 'Tools' && (
        <section className="cm-card">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <p className="cm-kicker">Tools</p>
              <h3 className="cm-section-title">개인 도구함 Tool Control</h3>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
                Service catalog는 유지하면서 개별 tool의 노출/호출 권한을 조정합니다. disabled/hidden은 외부 client에 안전하게 제한됩니다.
              </p>
            </div>
            <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
              <span className="rounded-lg bg-emerald-50 px-3 py-2 font-medium text-emerald-700 ring-1 ring-emerald-200">callable {toolControlSummary.callable}</span>
              <span className="rounded-lg bg-amber-50 px-3 py-2 font-medium text-amber-700 ring-1 ring-amber-200">visible {toolControlSummary.visibleOnly}</span>
              <span className="rounded-lg bg-muted px-3 py-2 font-medium text-muted-foreground ring-1 ring-border">hidden {toolControlSummary.hidden}</span>
              <span className="rounded-lg bg-rose-50 px-3 py-2 font-medium text-rose-700 ring-1 ring-rose-200">disabled {toolControlSummary.disabled}</span>
            </div>
          </div>

          <div className="mt-4 grid gap-3 rounded-xl border border-border bg-card p-3 md:grid-cols-3">
            {toolPresetOptions.map((option) => (
              <button
                key={option.value}
                type="button"
                disabled={Boolean(applyingPreset) || tools.length === 0}
                onClick={() => handleApplyPreset(option.value)}
                className="rounded-lg border border-border bg-background px-3 py-3 text-left transition hover:border-brand-300 hover:bg-brand-50 disabled:cursor-wait disabled:opacity-60 dark:hover:bg-brand-950/30"
              >
                <span className="text-sm font-medium text-foreground">{applyingPreset === option.value ? 'Applying…' : option.label}</span>
                <span className="mt-1 block text-xs leading-5 text-muted-foreground">{option.description}</span>
              </button>
            ))}
          </div>

          <div className="mt-6 grid gap-3">
            {tools.map((tool) => {
              const override = findOverrideForTool(tool, toolOverrides);
              const enabled = override?.enabled ?? true;
              const permissionLevel = override?.permission_level ?? 'callable';
              const permissionDescription = permissionOptions.find((option) => option.value === permissionLevel)?.description ?? '';
              const warningTotal = tool.warning_count ?? tool.warnings?.length ?? 0;
              const isUpdating = updatingToolId === tool.id;

              return (
                <div key={tool.id} className="cm-panel-subtle">
                  <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
                    <div className="flex min-w-0 items-start gap-3">
                      <ToolIcon tool={{ name: tool.original_name, title: tool.title ?? tool.original_name, icons: tool.icons_json ?? [] }} />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="font-medium text-foreground">{tool.title ?? tool.original_name}</p>
                          <ToolControlBadge enabled={enabled} permissionLevel={permissionLevel} />
                          <span className={classNames('rounded-full px-2.5 py-1 text-xs font-medium ring-1', riskPill(tool.risk_level))}>{tool.risk_level ?? 'unknown risk'}</span>
                          {tool.validation_status && <span className={classNames('rounded-full px-2.5 py-1 text-xs font-medium ring-1', statusPill(tool.validation_status))}>{tool.validation_status}</span>}
                          {warningTotal > 0 && <span className="rounded-full bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700 ring-1 ring-amber-200">warnings {warningTotal}</span>}
                        </div>
                        <p className="mt-1 font-mono text-xs text-muted-foreground">{tool.exposed_name ?? tool.original_name}</p>
                        {tool.description && <p className="cm-copy">{tool.description}</p>}
                        <dl className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-3">
                          <div><dt className="font-medium text-muted-foreground">schema_hash</dt><dd className="mt-1 break-all font-mono text-foreground">{tool.schema_hash ?? 'API 제공 없음'}</dd></div>
                          <div><dt className="font-medium text-muted-foreground">service_tool_id</dt><dd className="mt-1 break-all font-mono text-foreground">{tool.id}</dd></div>
                          <div><dt className="font-medium text-muted-foreground">updated</dt><dd className="mt-1 break-all font-mono text-foreground">{override?.updated_at ?? 'default policy'}</dd></div>
                        </dl>
                      </div>
                    </div>

                    <div className="grid gap-3 cm-panel p-3 xl:w-80">
                      <div className="flex items-center justify-between gap-3">
                        <div>
                          <p className="text-sm font-medium text-foreground">Enabled</p>
                          <p className="mt-1 text-xs text-muted-foreground">Service-level 도구함 상태와 별개인 tool-level override입니다.</p>
                        </div>
                        <button
                          type="button"
                          aria-pressed={enabled}
                          disabled={isUpdating}
                          onClick={() => handleToolControlChange(tool, { enabled: !enabled })}
                          className={classNames(
                            'rounded-lg px-3 py-2 text-xs font-medium transition disabled:cursor-wait disabled:opacity-60',
                            enabled ? 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200 hover:bg-emerald-100' : 'bg-muted text-muted-foreground ring-1 ring-border hover:bg-muted'
                          )}
                        >
                          {enabled ? 'On' : 'Off'}
                        </button>
                      </div>
                      <label className="grid gap-2 text-sm font-medium text-foreground">
                        Permission level
                        <select
                          value={permissionLevel}
                          disabled={isUpdating}
                          onChange={(event) => handleToolControlChange(tool, { permission_level: event.target.value as ToolPermissionLevel })}
                          className="cm-select disabled:cursor-wait disabled:bg-muted"
                        >
                          {permissionOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                        </select>
                      </label>
                      <p className="rounded-lg bg-muted px-3 py-2 text-xs leading-5 text-muted-foreground">{permissionDescription}</p>
                    </div>
                  </div>
                  <pre className="mt-4 max-h-52 overflow-auto rounded-lg bg-card p-3 text-xs text-foreground ring-1 ring-border">{JSON.stringify(tool.input_schema_json ?? {}, null, 2)}</pre>
                </div>
              );
            })}
            {tools.length === 0 && (
              <div className="cm-empty">
                <h4 className="font-medium text-foreground">아직 cached tool이 없습니다.</h4>
                <p className="cm-copy">Validation을 실행해 downstream MCP의 tools/list를 가져온 뒤 tool-level 권한을 조정하세요.</p>
                <button type="button" onClick={handleValidate} disabled={isValidating} className="mt-4 cm-button cm-button-brand disabled:cursor-not-allowed disabled:opacity-60">{isValidating ? '검증 중...' : 'Validate 실행'}</button>
              </div>
            )}
          </div>
        </section>
      )}

      {activeTab === 'Validation' && (
        <section className="cm-card">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <p className="cm-kicker">Validation</p>
              <h3 className="cm-section-title">검증 파이프라인</h3>
              <p className="cm-copy">SSRF guard를 먼저 통과한 뒤 MCP initialize와 tools/list를 실행합니다.</p>
            </div>
            <button type="button" onClick={handleValidate} disabled={isValidating} className="cm-button cm-button-primary disabled:cursor-not-allowed disabled:opacity-60">{isValidating ? '검증 중...' : 'Run validation'}</button>
          </div>
          <ol className="mt-4 grid gap-3 md:grid-cols-5">
            {validationStages.map((stage, index) => <li key={stage} className="cm-panel-subtle"><span className="text-xs font-medium text-brand-700">0{index + 1}</span><p className="mt-2 text-sm font-medium text-foreground">{stage}</p></li>)}
          </ol>
          <dl className="mt-6 grid gap-3 md:grid-cols-4">
            <MetadataCard label="schema_hash" value={schemaHash ?? 'API 제공 없음'} />
            <MetadataCard label="risk_level" value={riskLevel} tone={riskLevel === 'high' || riskLevel === 'dangerous' ? 'danger' : 'default'} />
            <MetadataCard label="validation" value={validationState} tone={validationState === 'error' ? 'danger' : 'default'} />
            <MetadataCard label="warnings" value={`${warningCount}`} tone={warningCount > 0 ? 'warning' : 'success'} />
          </dl>
          {validationWarnings.length > 0 && (
            <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-4">
              <h4 className="text-sm font-medium text-amber-950">Validation warnings</h4>
              <ul className="mt-3 grid gap-2 text-sm leading-6 text-amber-900">
                {validationWarnings.map((warning, index) => <li key={`${warning}-${index}`}>• {warning}</li>)}
              </ul>
            </div>
          )}
          {(schemaDiff.added.length > 0 || schemaDiff.removed.length > 0 || schemaDiff.changed.length > 0) && (
            <div className="mt-4 grid gap-3 lg:grid-cols-3">
              {[
                ['Added tools', schemaDiff.added, 'bg-emerald-50 text-emerald-950 ring-emerald-200'],
                ['Removed tools', schemaDiff.removed, 'bg-rose-50 text-rose-950 ring-rose-200'],
                ['Changed schemas', schemaDiff.changed, 'bg-amber-50 text-amber-950 ring-amber-200']
              ].map(([label, items, tone]) => (
                <div key={label as string} className={`rounded-lg p-4 ring-1 ${tone as string}`}>
                  <h4 className="text-sm font-medium">{label as string}</h4>
                  <ul className="mt-3 grid gap-2 text-xs leading-5">
                    {(items as Array<Record<string, unknown>>).map((item, index) => (
                      <li key={`${label}-${String(item.name)}-${index}`} className="rounded-md bg-background/70 p-2 font-mono text-foreground ring-1 ring-border">
                        <span className="block break-all">{textFromUnknown(item.name) ?? 'unknown'}</span>
                        <span className="mt-1 block break-all text-muted-foreground">
                          {textFromUnknown(item.schema_hash)
                            ?? textFromUnknown(item.current_schema_hash)
                            ?? textFromUnknown(item.previous_schema_hash)
                            ?? 'hash n/a'}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
          <pre className="mt-6 cm-code-block">{JSON.stringify(service?.validation_summary ?? { last_validated_at: service?.last_validated_at ?? null }, null, 2)}</pre>
        </section>
      )}

      {activeTab === 'Credential' && (
        <section className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
          <article className="cm-card">
            <p className="cm-kicker">Credential</p>
            <h3 className="cm-section-title">Vault 연결 상태</h3>
            <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-950">
              Downstream credential만 vault에 저장합니다. CoreMCP admin/client token은 downstream으로 전달하지 않습니다.
            </div>
            <dl className="mt-5 grid gap-3">
              <MetadataCard label="Status" value={credential?.status ?? service?.credential_status ?? 'unknown'} />
              <MetadataCard label="Masked" value={credential?.masked ?? service?.credential_masked ?? '—'} />
              <MetadataCard label="Updated" value={credential?.updated_at ?? '—'} />
            </dl>
          </article>
          <article className="cm-card">
            <h3 className="cm-section-title">Credential 등록/회전</h3>
            <p className="cm-copy">새 secret 입력 후 저장하면 vault에만 반영되고 평문은 브라우저 state에서 제거됩니다.</p>
            <form onSubmit={handleCredentialSubmit} className="mt-4 grid gap-3 cm-panel-subtle">
              <label className="grid gap-2 text-sm font-medium text-foreground">Credential type
                <select value={credentialType} onChange={(event) => setCredentialType(event.target.value)} className="cm-select">
                  <option value="bearer_token">bearer_token</option>
                  <option value="api_key_header">api_key_header</option>
                  <option value="service_account">service_account</option>
                </select>
              </label>
              <label className="grid gap-2 text-sm font-medium text-foreground">Header name
                <input value={headerName} onChange={(event) => setHeaderName(event.target.value)} className="cm-input" placeholder="Authorization or x-api-key" />
              </label>
              <label className="grid gap-2 text-sm font-medium text-foreground">Secret
                <input type="password" value={secretInput} onChange={(event) => setSecretInput(event.target.value)} className="cm-input" placeholder="평문은 응답/로그에 표시되지 않습니다" autoComplete="off" />
              </label>
              <div className="flex flex-wrap gap-2">
                <button type="submit" className="cm-button cm-button-primary">저장/회전</button>
                <button type="button" onClick={handleDeleteCredential} className="cm-button cm-button-danger">Credential 삭제</button>
              </div>
            </form>
          </article>
        </section>
      )}

      {activeTab === 'Logs' && (
        <section className="grid gap-4 xl:grid-cols-2">
          <article className="cm-card">
            <p className="cm-kicker">Logs</p>
            <h3 className="cm-section-title">관련 tool invocation</h3>
            <p className="cm-copy">request_id, service, tool, status/error_code를 함께 확인합니다.</p>
            <div className="mt-4 grid gap-3">
              {invocations.map((item) => <InvocationLogCard key={item.id} item={item} />)}
              {invocations.length === 0 && <p className="text-sm text-muted-foreground">이 service 관련 호출 기록이 없습니다.</p>}
            </div>
          </article>
          <article className="cm-card">
            <p className="cm-kicker">Audit</p>
            <h3 className="cm-section-title">관련 audit events</h3>
            <p className="cm-copy">policy deny, auth failure, downstream error를 error_code와 연결해 봅니다.</p>
            <div className="mt-4 grid gap-3">
              {auditLogs.map((item) => <AuditLogCard key={item.id} item={item} />)}
              {auditLogs.length === 0 && <p className="text-sm text-muted-foreground">이 service 관련 audit 기록이 없습니다.</p>}
            </div>
          </article>
        </section>
      )}

      {activeTab === 'Settings' && (
        <section className="cm-card">
          <p className="cm-kicker">Settings</p>
          <h3 className="cm-section-title">Service 설정과 삭제</h3>
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <form onSubmit={handleMetadataSubmit} className="cm-panel-subtle">
              <h4 className="font-medium text-foreground">Private service metadata</h4>
              <p className="cm-copy">개인 도구함에서 service를 빠르게 식별하기 위한 로컬 전용 metadata입니다.</p>
              <div className="mt-4 grid gap-3">
                <label className="grid gap-2 text-sm font-medium text-foreground">Category
                  <input
                    value={metadataDraft.category}
                    onChange={(event) => setMetadataDraft((previous) => ({ ...previous, category: event.target.value }))}
                    className="cm-input"
                    placeholder="productivity, infra, knowledge"
                  />
                </label>
                <label className="grid gap-2 text-sm font-medium text-foreground">Homepage URL
                  <input
                    value={metadataDraft.homepage_url}
                    onChange={(event) => setMetadataDraft((previous) => ({ ...previous, homepage_url: event.target.value }))}
                    className="cm-input"
                    placeholder="https://example.com"
                  />
                </label>
                <label className="grid gap-2 text-sm font-medium text-foreground">Documentation URL
                  <input
                    value={metadataDraft.documentation_url}
                    onChange={(event) => setMetadataDraft((previous) => ({ ...previous, documentation_url: event.target.value }))}
                    className="cm-input"
                    placeholder="https://example.com/docs"
                  />
                </label>
                <label className="grid gap-2 text-sm font-medium text-foreground">Logo URL
                  <input
                    value={metadataDraft.logo_url}
                    onChange={(event) => setMetadataDraft((previous) => ({ ...previous, logo_url: event.target.value }))}
                    className="cm-input"
                    placeholder="https://example.com/icon.png"
                  />
                </label>
              </div>
              <button type="submit" className="mt-4 cm-button cm-button-primary">Metadata 저장</button>
            </form>
            <div className="cm-panel-subtle">
              <h4 className="font-medium text-foreground">운영 메모</h4>
              <p className="cm-copy">Endpoint, auth_type, credential 상태를 바꾼 뒤에는 validation을 다시 실행해 cached tool catalog를 갱신하세요.</p>
              <p className="mt-3 font-mono text-xs text-muted-foreground">updated_at: {service?.updated_at ?? '—'}</p>
            </div>
            <div className="rounded-lg border border-rose-200 bg-rose-50 p-4">
              <h4 className="font-medium text-rose-950">Service 삭제</h4>
              <p className="mt-2 text-sm leading-6 text-rose-900">삭제는 soft-delete이며 도구함 item도 제거됩니다. 실행하려면 slug 또는 id를 입력하세요.</p>
              <input value={deleteConfirm} onChange={(event) => setDeleteConfirm(event.target.value)} className="mt-3 cm-input border-rose-200" placeholder={service?.slug ?? service?.id ?? serviceId} />
              <button type="button" disabled={!canDelete} onClick={handleDeleteService} className="mt-3 cm-button cm-button-danger bg-rose-600 text-white hover:bg-rose-700 disabled:bg-rose-200">Service 삭제</button>
            </div>
          </div>
        </section>
      )}
    </AdminShell>
  );
}
