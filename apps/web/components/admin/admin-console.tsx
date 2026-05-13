'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';
import {
  coreMcpApi,
  clearAdminToken,
  getStoredAdminToken,
  saveAdminToken,
  type AuditLogSummary,
  type ClientTokenSummary,
  type ExternalConnectionSummary,
  type McpServiceSummary,
  type PlaygroundToolSummary,
  type SettingsResponse,
  type ToolboxItemSummary,
  type ToolboxSummary,
  type ToolInvocationSummary,
  type ToolOverrideSummary
} from '@/lib/api';
import { AdminShell } from './admin-shell';
import { explainError, maskToken } from './admin-utils';
import { ClientsSection } from './sections/clients-section';
import { DashboardSection } from './sections/dashboard-section';
import { LogsSection } from './sections/logs-section';
import { PlaygroundSection } from './sections/playground-section';
import { ServicesSection } from './sections/services-section';
import { SettingsSection } from './sections/settings-section';
import { ToolboxSection } from './sections/toolbox-section';

export function AdminConsole({ initialSection = 'dashboard' }: { initialSection?: string }) {
  const [token, setToken] = useState<string | null>(null);
  const [tokenInput, setTokenInput] = useState('');
  const [healthMessage, setHealthMessage] = useState('API 상태를 아직 확인하지 않았습니다.');
  const [statusMessage, setStatusMessage] = useState('Admin token 저장 후 데이터를 불러오세요.');

  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [services, setServices] = useState<McpServiceSummary[]>([]);
  const [toolboxes, setToolboxes] = useState<ToolboxSummary[]>([]);
  const [defaultToolbox, setDefaultToolbox] = useState<ToolboxSummary | null>(null);
  const [connections, setConnections] = useState<ExternalConnectionSummary[]>([]);
  const [clientTokens, setClientTokens] = useState<ClientTokenSummary[]>([]);
  const [invocations, setInvocations] = useState<ToolInvocationSummary[]>([]);
  const [auditLogs, setAuditLogs] = useState<AuditLogSummary[]>([]);
  const [playgroundTools, setPlaygroundTools] = useState<PlaygroundToolSummary[]>([]);
  const [toolOverridesByService, setToolOverridesByService] = useState<Record<string, ToolOverrideSummary[]>>({});

  const [serviceName, setServiceName] = useState('Fake MCP');
  const [serviceSlug, setServiceSlug] = useState('fake');
  const [serviceUrl, setServiceUrl] = useState('http://127.0.0.1:8790/mcp');
  const [clientName, setClientName] = useState('Codex CLI exec (local)');
  const [clientType, setClientType] = useState('codex_cli');
  const [issuedToken, setIssuedToken] = useState<string | null>(null);
  const [selectedTool, setSelectedTool] = useState('');
  const [argumentsJson, setArgumentsJson] = useState('{\n  "message": "hello"\n}');
  const [callResult, setCallResult] = useState('도구를 선택하고 호출하면 응답 JSON이 표시됩니다.');

  useEffect(() => {
    const stored = getStoredAdminToken();
    setToken(stored);
    setTokenInput(stored ?? '');

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
      void refreshAdminData();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const tokenPreview = useMemo(() => maskToken(token), [token]);
  const toolboxItems = (defaultToolbox?.items ?? []) as ToolboxItemSummary[];
  const activeSection = ['dashboard', 'services', 'toolbox', 'clients', 'settings', 'playground', 'logs'].includes(initialSection)
    ? initialSection
    : 'dashboard';

  async function refreshAdminData() {
    if (!getStoredAdminToken()) return;
    setStatusMessage('CoreMCP 데이터를 불러오는 중입니다...');
    try {
      const [settingsResponse, servicesResponse, toolboxesResponse, connectionsResponse, tokensResponse, invocationsResponse, auditResponse] = await Promise.all([
        coreMcpApi.settings(),
        coreMcpApi.listServices(),
        coreMcpApi.listToolboxes(),
        coreMcpApi.listExternalConnections(),
        coreMcpApi.listClientTokens(),
        coreMcpApi.listToolInvocations(),
        coreMcpApi.listAuditLogs()
      ]);
      setSettings(settingsResponse);
      setServices(servicesResponse.items);
      setToolboxes(toolboxesResponse.items);
      setConnections(connectionsResponse.items);
      setClientTokens(tokensResponse.items);
      setInvocations(invocationsResponse.items);
      setAuditLogs(auditResponse.items);
      const firstToolbox = toolboxesResponse.items.find((item) => item.is_default) ?? toolboxesResponse.items[0];
      const defaultToolboxResponse = firstToolbox ? await coreMcpApi.getToolbox(firstToolbox.id) : null;
      setDefaultToolbox(defaultToolboxResponse);
      const nextOverridesByService: Record<string, ToolOverrideSummary[]> = {};
      await Promise.all((defaultToolboxResponse?.items ?? []).map(async (item) => {
        const response = await coreMcpApi.listToolOverrides(item.service_id).catch(() => ({ items: [], next_cursor: null }));
        nextOverridesByService[item.service_id] = response.items;
      }));
      setToolOverridesByService(nextOverridesByService);
      setStatusMessage('최신 데이터를 불러왔습니다.');
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
    setHealthMessage('admin token을 sessionStorage에 저장했습니다. API 호출에 Authorization 헤더가 포함됩니다.');
  }

  function handleTokenClear() {
    clearAdminToken();
    setToken(null);
    setTokenInput('');
    setSettings(null);
    setServices([]);
    setToolboxes([]);
    setDefaultToolbox(null);
    setConnections([]);
    setClientTokens([]);
    setAuditLogs([]);
    setInvocations([]);
    setToolOverridesByService({});
    setIssuedToken(null);
    setHealthMessage('sessionStorage에 저장된 admin token을 삭제했습니다.');
  }

  async function handleHealthCheck() {
    setHealthMessage('API 상태를 확인하는 중입니다...');
    try {
      const health = await coreMcpApi.health();
      setHealthMessage(`API 상태: ${health.status}`);
    } catch (error) {
      setHealthMessage(explainError(error));
    }
  }

  async function handleCreateService(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setStatusMessage('MCP service를 생성하는 중입니다...');
    try {
      const created = await coreMcpApi.createService({
        name: serviceName,
        slug: serviceSlug,
        endpoint_url: serviceUrl,
        auth_type: 'none'
      });
      setStatusMessage(`Service 생성 완료: ${created.slug}. 검증을 실행하세요.`);
      await refreshAdminData();
    } catch (error) {
      setStatusMessage(explainError(error));
    }
  }

  async function handleValidate(serviceId: string) {
    setStatusMessage('Validation을 실행하는 중입니다...');
    try {
      const report = await coreMcpApi.validateService(serviceId);
      setStatusMessage(`Validation ${report.status}: tools ${report.tools_found}개`);
      await refreshAdminData();
    } catch (error) {
      setStatusMessage(explainError(error));
      await refreshAdminData();
    }
  }

  async function handleAddToToolbox(serviceId: string) {
    if (!defaultToolbox) return;
    setStatusMessage('기본 도구함에 추가하는 중입니다...');
    try {
      await coreMcpApi.addToolboxItem(defaultToolbox.id, serviceId);
      setStatusMessage('기본 도구함에 추가했습니다.');
      await refreshAdminData();
    } catch (error) {
      setStatusMessage(explainError(error));
    }
  }

  async function handleToggleToolboxItem(item: ToolboxItemSummary) {
    if (!defaultToolbox) return;
    setStatusMessage('Toolbox item 상태를 변경하는 중입니다...');
    try {
      await coreMcpApi.updateToolboxItem(defaultToolbox.id, item.id, !Boolean(item.enabled));
      setStatusMessage('Toolbox item 상태를 변경했습니다.');
      await refreshAdminData();
    } catch (error) {
      setStatusMessage(explainError(error));
    }
  }

  async function handleRemoveToolboxItem(item: ToolboxItemSummary) {
    if (!defaultToolbox) return;
    setStatusMessage('Toolbox에서 제거하는 중입니다...');
    try {
      await coreMcpApi.removeToolboxItem(defaultToolbox.id, item.id);
      setStatusMessage('Toolbox에서 제거했습니다.');
      await refreshAdminData();
    } catch (error) {
      setStatusMessage(explainError(error));
    }
  }

  async function handleRevokeConnection(connectionId: string) {
    setStatusMessage('External connection을 revoke하는 중입니다...');
    try {
      await coreMcpApi.revokeExternalConnection(connectionId);
      setStatusMessage('External connection과 연결된 token을 revoke했습니다.');
      await refreshAdminData();
    } catch (error) {
      setStatusMessage(explainError(error));
    }
  }

  async function handleRevokeClientToken(tokenId: string) {
    setStatusMessage('Client token을 revoke하는 중입니다...');
    try {
      await coreMcpApi.revokeClientToken(tokenId);
      setStatusMessage('Client token을 revoke했습니다.');
      await refreshAdminData();
    } catch (error) {
      setStatusMessage(explainError(error));
    }
  }

  async function handleCreateConnection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setStatusMessage('External connection을 생성하는 중입니다...');
    try {
      const connection = await coreMcpApi.createExternalConnection({ client_type: clientType, client_name: clientName });
      const tokenResponse = await coreMcpApi.issueClientToken(connection.id);
      setIssuedToken(tokenResponse.token);
      setStatusMessage('Client token을 발급했습니다. 평문은 지금 한 번만 표시됩니다.');
      await refreshAdminData();
    } catch (error) {
      setStatusMessage(explainError(error));
    }
  }

  async function handleCreateOneTimeToken() {
    setStatusMessage('One-time connection token을 발급하는 중입니다...');
    try {
      const response = await coreMcpApi.createOneTimeConnectionToken({
        client_type: clientType,
        requested_scopes: ['mcp:tools.read', 'mcp:tools.call']
      });
      setIssuedToken(`${response.connection_prompt}\n\n${response.token}`);
      setStatusMessage(`One-time token을 발급했습니다. ${response.expires_at}까지 1회만 사용할 수 있습니다.`);
    } catch (error) {
      setStatusMessage(explainError(error));
    }
  }

  async function handleLoadPlaygroundTools() {
    setCallResult('도구 목록을 불러오는 중입니다...');
    try {
      const response = await coreMcpApi.listPlaygroundTools();
      setPlaygroundTools(response.items);
      setSelectedTool(response.items[0]?.name ?? '');
      setCallResult(`사용 가능한 도구 ${response.items.length}개를 불러왔습니다.`);
    } catch (error) {
      setCallResult(explainError(error));
    }
  }

  async function handleCallTool() {
    if (!selectedTool) return;
    try {
      const parsed = JSON.parse(argumentsJson) as Record<string, unknown>;
      setCallResult('도구를 호출하는 중입니다...');
      const result = await coreMcpApi.callPlaygroundTool(selectedTool, parsed);
      setCallResult(JSON.stringify(result, null, 2));
      await refreshAdminData();
    } catch (error) {
      setCallResult(explainError(error));
    }
  }

  function renderActiveSection() {
    if (activeSection === 'dashboard') {
      return <DashboardSection defaultToolbox={defaultToolbox} toolboxItems={toolboxItems} services={services} settings={settings} clientTokens={clientTokens} invocations={invocations} />;
    }

    if (activeSection === 'services') {
      return (
        <ServicesSection
          services={services}
          serviceName={serviceName}
          serviceSlug={serviceSlug}
          serviceUrl={serviceUrl}
          onServiceNameChange={setServiceName}
          onServiceSlugChange={setServiceSlug}
          onServiceUrlChange={setServiceUrl}
          onCreateService={handleCreateService}
          onValidate={handleValidate}
          onAddToToolbox={handleAddToToolbox}
        />
      );
    }

    if (activeSection === 'toolbox') {
      return (
        <ToolboxSection
          toolboxes={toolboxes}
          toolboxItems={toolboxItems}
          toolOverridesByService={toolOverridesByService}
          onToggleToolboxItem={handleToggleToolboxItem}
          onRemoveToolboxItem={handleRemoveToolboxItem}
        />
      );
    }

    if (activeSection === 'clients') {
      return (
        <ClientsSection
          connections={connections}
          clientName={clientName}
          clientType={clientType}
          issuedToken={issuedToken}
          onClientNameChange={setClientName}
          onClientTypeChange={setClientType}
          onCreateConnection={handleCreateConnection}
          onCreateOneTimeToken={handleCreateOneTimeToken}
          onRevokeConnection={handleRevokeConnection}
        />
      );
    }

    if (activeSection === 'settings') {
      return <SettingsSection tokenPreview={tokenPreview} clientTokens={clientTokens} onRevokeClientToken={handleRevokeClientToken} />;
    }

    if (activeSection === 'playground') {
      return (
        <PlaygroundSection
          playgroundTools={playgroundTools}
          selectedTool={selectedTool}
          argumentsJson={argumentsJson}
          callResult={callResult}
          onLoadPlaygroundTools={handleLoadPlaygroundTools}
          onSelectedToolChange={setSelectedTool}
          onArgumentsJsonChange={setArgumentsJson}
          onCallTool={handleCallTool}
        />
      );
    }

    return <LogsSection invocations={invocations} auditLogs={auditLogs} />;
  }

  return (
    <AdminShell
      activeSection={activeSection}
      statusMessage={statusMessage}
      token={token}
      tokenInput={tokenInput}
      tokenPreview={tokenPreview}
      healthMessage={healthMessage}
      onTokenInputChange={setTokenInput}
      onTokenSubmit={handleTokenSubmit}
      onTokenClear={handleTokenClear}
      onHealthCheck={handleHealthCheck}
      onRefresh={refreshAdminData}
    >
      {renderActiveSection()}
    </AdminShell>
  );
}
