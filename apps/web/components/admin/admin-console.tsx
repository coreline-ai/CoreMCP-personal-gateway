'use client';

import { FormEvent, useEffect, useMemo, useReducer, useState } from 'react';
import {
  coreMcpApi,
  clearAdminToken,
  getStoredAdminToken,
  saveAdminToken,
  type AuditLogSummary,
  type ClientTokenSummary,
  type DashboardSummary,
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
import { SimulatorSection } from './sections/simulator-section';
import { ToolboxSection } from './sections/toolbox-section';

const HEALTH_CHECK_PROMPT = 'Health 버튼을 눌러 API 상태를 확인하세요.';

interface DataState {
  settings: SettingsResponse | null;
  services: McpServiceSummary[];
  toolboxes: ToolboxSummary[];
  defaultToolbox: ToolboxSummary | null;
  connections: ExternalConnectionSummary[];
  clientTokens: ClientTokenSummary[];
  invocations: ToolInvocationSummary[];
  auditLogs: AuditLogSummary[];
  dashboardSummary: DashboardSummary | null;
  playgroundTools: PlaygroundToolSummary[];
  toolOverridesByService: Record<string, ToolOverrideSummary[]>;
}

type DataAction =
  | { type: 'LOAD_ALL'; payload: Omit<DataState, 'playgroundTools'> }
  | { type: 'RESET' }
  | { type: 'SET_PLAYGROUND_TOOLS'; payload: PlaygroundToolSummary[] };

const initialDataState: DataState = {
  settings: null,
  services: [],
  toolboxes: [],
  defaultToolbox: null,
  connections: [],
  clientTokens: [],
  invocations: [],
  auditLogs: [],
  dashboardSummary: null,
  playgroundTools: [],
  toolOverridesByService: {}
};

function dataReducer(state: DataState, action: DataAction): DataState {
  switch (action.type) {
    case 'LOAD_ALL':
      return { ...state, ...action.payload };
    case 'RESET':
      return initialDataState;
    case 'SET_PLAYGROUND_TOOLS':
      return { ...state, playgroundTools: action.payload };
  }
}

interface TokenState {
  token: string | null;
  tokenInput: string;
  mounted: boolean;
  healthMessage: string;
  statusMessage: string;
}

type TokenAction =
  | { type: 'MOUNT'; payload: { token: string | null } }
  | { type: 'SET_TOKEN'; payload: string }
  | { type: 'SET_TOKEN_INPUT'; payload: string }
  | { type: 'CLEAR_TOKEN' }
  | { type: 'SET_HEALTH_MESSAGE'; payload: string }
  | { type: 'SET_STATUS_MESSAGE'; payload: string };

const initialTokenState: TokenState = {
  token: null,
  tokenInput: '',
  mounted: false,
  healthMessage: HEALTH_CHECK_PROMPT,
  statusMessage: 'Admin token 저장 후 데이터를 불러오세요.'
};

function tokenReducer(state: TokenState, action: TokenAction): TokenState {
  switch (action.type) {
    case 'MOUNT':
      return {
        ...state,
        mounted: true,
        token: action.payload.token,
        tokenInput: action.payload.token ?? ''
      };
    case 'SET_TOKEN':
      return { ...state, token: action.payload };
    case 'SET_TOKEN_INPUT':
      return { ...state, tokenInput: action.payload };
    case 'CLEAR_TOKEN':
      return { ...state, token: null, tokenInput: '' };
    case 'SET_HEALTH_MESSAGE':
      return { ...state, healthMessage: action.payload };
    case 'SET_STATUS_MESSAGE':
      return { ...state, statusMessage: action.payload };
  }
}

interface FormState {
  serviceName: string;
  serviceSlug: string;
  serviceUrl: string;
  clientName: string;
  clientType: string;
  issuedToken: string | null;
  selectedTool: string;
  argumentsJson: string;
  callResult: string;
}

type FormFieldKey = Exclude<keyof FormState, 'issuedToken'>;

type FormAction =
  | { type: 'UPDATE_FIELD'; field: FormFieldKey; value: string }
  | { type: 'SET_ISSUED_TOKEN'; payload: string | null }
  | { type: 'SET_CALL_RESULT'; payload: string }
  | { type: 'SET_SELECTED_TOOL'; payload: string };

const initialFormState: FormState = {
  serviceName: 'Fake MCP',
  serviceSlug: 'fake',
  serviceUrl: 'http://127.0.0.1:8790/mcp',
  clientName: 'Codex CLI exec (local)',
  clientType: 'codex_cli',
  issuedToken: null,
  selectedTool: '',
  argumentsJson: '{\n  "message": "hello"\n}',
  callResult: '도구를 선택하고 호출하면 응답 JSON이 표시됩니다.'
};

function formReducer(state: FormState, action: FormAction): FormState {
  switch (action.type) {
    case 'UPDATE_FIELD':
      return { ...state, [action.field]: action.value };
    case 'SET_ISSUED_TOKEN':
      return { ...state, issuedToken: action.payload };
    case 'SET_CALL_RESULT':
      return { ...state, callResult: action.payload };
    case 'SET_SELECTED_TOOL':
      return { ...state, selectedTool: action.payload };
  }
}

export function AdminConsole({ initialSection = 'dashboard' }: { initialSection?: string }) {
  const [tokenState, dispatchToken] = useReducer(tokenReducer, initialTokenState);
  const { token, tokenInput, mounted, healthMessage, statusMessage } = tokenState;
  const setToken = (value: string | null) => {
    if (value === null) {
      dispatchToken({ type: 'CLEAR_TOKEN' });
    } else {
      dispatchToken({ type: 'SET_TOKEN', payload: value });
    }
  };
  const setTokenInput = (value: string) => dispatchToken({ type: 'SET_TOKEN_INPUT', payload: value });
  const setHealthMessage = (value: string) => dispatchToken({ type: 'SET_HEALTH_MESSAGE', payload: value });
  const setStatusMessage = (value: string) => dispatchToken({ type: 'SET_STATUS_MESSAGE', payload: value });

  const [data, dispatchData] = useReducer(dataReducer, initialDataState);
  const {
    settings,
    services,
    toolboxes,
    defaultToolbox,
    connections,
    clientTokens,
    invocations,
    auditLogs,
    dashboardSummary,
    playgroundTools,
    toolOverridesByService
  } = data;

  const [formState, dispatchForm] = useReducer(formReducer, initialFormState);
  const { serviceName, serviceSlug, serviceUrl, clientName, clientType, issuedToken, selectedTool, argumentsJson, callResult } = formState;
  const setServiceName = (value: string) => dispatchForm({ type: 'UPDATE_FIELD', field: 'serviceName', value });
  const setServiceSlug = (value: string) => dispatchForm({ type: 'UPDATE_FIELD', field: 'serviceSlug', value });
  const setServiceUrl = (value: string) => dispatchForm({ type: 'UPDATE_FIELD', field: 'serviceUrl', value });
  const setClientName = (value: string) => dispatchForm({ type: 'UPDATE_FIELD', field: 'clientName', value });
  const setClientType = (value: string) => dispatchForm({ type: 'UPDATE_FIELD', field: 'clientType', value });
  const setIssuedToken = (value: string | null) => dispatchForm({ type: 'SET_ISSUED_TOKEN', payload: value });
  const setSelectedTool = (value: string) => dispatchForm({ type: 'SET_SELECTED_TOOL', payload: value });
  const setArgumentsJson = (value: string) => dispatchForm({ type: 'UPDATE_FIELD', field: 'argumentsJson', value });
  const setCallResult = (value: string) => dispatchForm({ type: 'SET_CALL_RESULT', payload: value });
  const [validatingServiceIds, setValidatingServiceIds] = useState<string[]>([]);

  const tokenPreview = useMemo(() => maskToken(token), [token]);
  const toolboxItems = (defaultToolbox?.items ?? []) as ToolboxItemSummary[];
  const activeSection = ['dashboard', 'services', 'toolbox', 'clients', 'settings', 'playground', 'simulator', 'logs'].includes(initialSection)
    ? initialSection
    : 'dashboard';

  useEffect(() => {
    const stored = getStoredAdminToken();
    dispatchToken({ type: 'MOUNT', payload: { token: stored } });

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

  useEffect(() => {
    if (!token || activeSection !== 'playground' || playgroundTools.length > 0) return;
    void handleLoadPlaygroundTools();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, activeSection, playgroundTools.length]);

  async function refreshAdminData() {
    if (!getStoredAdminToken()) return;
    setStatusMessage('CoreMCP 데이터를 불러오는 중입니다...');
    try {
      const [settingsResponse, dashboardResponse, servicesResponse, toolboxesResponse, connectionsResponse, tokensResponse, invocationsResponse, auditResponse] = await Promise.all([
        coreMcpApi.settings(),
        coreMcpApi.dashboardSummary(),
        coreMcpApi.listServices(),
        coreMcpApi.listToolboxes(),
        coreMcpApi.listExternalConnections(),
        coreMcpApi.listClientTokens(),
        coreMcpApi.listToolInvocations(),
        coreMcpApi.listAuditLogs()
      ]);
      const firstToolbox = toolboxesResponse.items.find((item) => item.is_default) ?? toolboxesResponse.items[0];
      const defaultToolboxResponse = firstToolbox ? await coreMcpApi.getToolbox(firstToolbox.id) : null;
      const nextOverridesByService: Record<string, ToolOverrideSummary[]> = {};
      await Promise.all((defaultToolboxResponse?.items ?? []).map(async (item) => {
        const response = await coreMcpApi.listToolOverrides(item.service_id).catch(() => ({ items: [], next_cursor: null }));
        nextOverridesByService[item.service_id] = response.items;
      }));
      dispatchData({
        type: 'LOAD_ALL',
        payload: {
          settings: settingsResponse,
          dashboardSummary: dashboardResponse,
          services: servicesResponse.items,
          toolboxes: toolboxesResponse.items,
          connections: connectionsResponse.items,
          clientTokens: tokensResponse.items,
          invocations: invocationsResponse.items,
          auditLogs: auditResponse.items,
          defaultToolbox: defaultToolboxResponse,
          toolOverridesByService: nextOverridesByService
        }
      });
      setStatusMessage('최신 데이터를 불러왔습니다.');
    } catch (error) {
      setStatusMessage(explainError(error));
    }
  }

  function handleTokenSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextToken = tokenInput.trim();
    if (!nextToken) {
      setStatusMessage('Admin token을 입력해야 저장할 수 있습니다.');
      return;
    }
    saveAdminToken(nextToken);
    setToken(nextToken);
    void runHealthCheck('admin token을 저장했습니다. API 상태를 확인하는 중입니다...');
  }

  function handleTokenClear() {
    clearAdminToken();
    setToken(null);
    setTokenInput('');
    dispatchData({ type: 'RESET' });
    setIssuedToken(null);
    setHealthMessage('sessionStorage에 저장된 admin token을 삭제했습니다.');
    setStatusMessage('Admin token을 삭제했습니다. 새 token을 저장하면 데이터를 다시 불러옵니다.');
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
    if (validatingServiceIds.includes(serviceId)) return;
    setValidatingServiceIds((current) => current.includes(serviceId) ? current : [...current, serviceId]);
    setStatusMessage('Validation을 실행하는 중입니다...');
    try {
      const report = await coreMcpApi.validateService(serviceId);
      setStatusMessage(`Validation ${report.status}: tools ${report.tools_found}개`);
      await refreshAdminData();
    } catch (error) {
      setStatusMessage(explainError(error));
      await refreshAdminData();
    } finally {
      setValidatingServiceIds((current) => current.filter((id) => id !== serviceId));
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
    if (!window.confirm(`${item.service_name ?? item.service_slug ?? '이 service'}를 기본 도구함에서 제거할까요?`)) return;
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
    if (!window.confirm('이 AI client 연결과 관련 token을 revoke할까요?')) return;
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
    if (!window.confirm('이 client token을 revoke할까요?')) return;
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
    const trimmedClientName = clientName.trim();
    if (!trimmedClientName) {
      setStatusMessage('Client name을 입력해야 token을 발급할 수 있습니다.');
      return;
    }
    setStatusMessage('External connection을 생성하는 중입니다...');
    try {
      const connection = await coreMcpApi.createExternalConnection({ client_type: clientType, client_name: trimmedClientName });
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
      dispatchData({ type: 'SET_PLAYGROUND_TOOLS', payload: response.items });
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
      return <DashboardSection defaultToolbox={defaultToolbox} toolboxItems={toolboxItems} services={services} settings={settings} clientTokens={clientTokens} invocations={invocations} dashboardSummary={dashboardSummary} />;
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
          validatingServiceIds={validatingServiceIds}
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

    if (activeSection === 'simulator') {
      return <SimulatorSection token={token} />;
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
      mounted={mounted}
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
