export const ADMIN_TOKEN_STORAGE_KEY = 'coremcp_admin_token';

export type ApiErrorCode = 'unauthorized' | 'http_error' | 'network_error' | 'parse_error';

export interface ApiErrorPayload {
  error?: {
    code?: string;
    message?: string;
    details?: unknown;
  };
  request_id?: string;
}

export class ApiError extends Error {
  readonly code: ApiErrorCode;
  readonly status?: number;
  readonly requestId?: string;
  readonly details?: unknown;

  constructor(message: string, options: { code: ApiErrorCode; status?: number; requestId?: string; details?: unknown }) {
    super(message);
    this.name = 'ApiError';
    this.code = options.code;
    this.status = options.status;
    this.requestId = options.requestId;
    this.details = options.details;
  }
}

export interface HealthResponse {
  status: 'ok' | string;
}

export interface SettingsResponse {
  admin_token_masked?: string;
  client_token_count?: number;
  auth_mode?: string;
  oauth_enabled?: boolean;
  secret_backend?: string;
  tailscale_enabled?: boolean;
  cache_backend?: string;
  app_version?: string;
}

export interface ListResponse<T> {
  items: T[];
  next_cursor: string | null;
}

export interface McpServiceSummary {
  id: string;
  name: string;
  slug: string;
  endpoint_url?: string;
  description?: string | null;
  category?: string | null;
  logo_url?: string | null;
  homepage_url?: string | null;
  documentation_url?: string | null;
  auth_type?: string;
  status: 'draft' | 'validating' | 'active' | 'error' | 'disabled' | 'auth_required' | string;
  tool_count?: number;
  risk_level?: string | null;
  credential_status?: string | null;
  credential_masked?: string | null;
  validation_summary?: Record<string, unknown> | null;
  last_validated_at?: string | null;
  updated_at?: string | null;
}

export interface ServiceToolSummary {
  id: string;
  service_id: string;
  original_name: string;
  exposed_name?: string | null;
  title?: string | null;
  description?: string | null;
  status?: string;
  risk_level?: string | null;
  schema_hash?: string | null;
  validation_status?: string | null;
  warning_count?: number | null;
  warnings?: unknown[] | null;
  input_schema_json?: Record<string, unknown> | null;
  icons_json?: Array<{ src: string; mimeType?: string; sizes?: string[] }>;
}

export type ToolPermissionLevel = 'hidden' | 'visible_only' | 'callable';
export type ToolPreset = 'readonly' | 'full_access' | 'dangerous_off';

export interface ToolOverrideSummary {
  id: string;
  toolbox_id: string;
  service_id: string;
  service_tool_id: string;
  exposed_name: string;
  enabled: boolean;
  permission_level: ToolPermissionLevel;
  updated_at: string;
}

export interface ToolPresetResponse {
  preset: ToolPreset;
  items: ToolOverrideSummary[];
  counts: Record<string, number>;
  next_cursor: string | null;
}

export interface ServiceCredentialSummary {
  status: string;
  masked: string | null;
  updated_at: string | null;
}

export interface ToolboxSummary {
  id: string;
  name: string;
  is_default?: boolean;
  item_count?: number;
  items?: ToolboxItemSummary[];
}

export interface ToolboxItemSummary {
  id: string;
  toolbox_id: string;
  service_id: string;
  enabled: 0 | 1 | boolean;
  service_name?: string;
  service_slug?: string;
  service_status?: string;
  tool_count?: number;
  callable_tool_count?: number | null;
  visible_only_tool_count?: number | null;
  hidden_tool_count?: number | null;
  disabled_tool_count?: number | null;
}

export interface ExternalConnectionSummary {
  id: string;
  client_type: string;
  client_name: string;
  status: 'active' | 'revoked' | string;
  last_used_at?: string | null;
  created_at?: string | null;
}

export interface PlaygroundToolSummary {
  name: string;
  title?: string;
  description?: string;
  icons?: Array<{ src: string; mimeType?: string; sizes?: string[] }>;
}

export interface ClientTokenSummary {
  id: string;
  external_connection_id: string;
  token_prefix: string;
  scopes: string[];
  status: 'active' | 'revoked' | string;
  last_used_at?: string | null;
  created_at?: string | null;
  revoked_at?: string | null;
}

export interface IssueClientTokenResponse extends ClientTokenSummary {
  token: string;
}

export interface OneTimeConnectionTokenResponse {
  token: string;
  token_type: 'coremcp_one_time' | string;
  expires_in: number;
  expires_at: string;
  client_type: string;
  toolbox_id: string;
  requested_scopes: string[];
  connection_prompt: string;
}

export interface ToolInvocationSummary {
  id: string;
  request_id?: string | null;
  method?: string;
  exposed_tool_name?: string;
  service_id?: string | null;
  service_name?: string | null;
  service_slug?: string | null;
  service_tool_id?: string | null;
  tool_name?: string | null;
  status: string;
  latency_ms?: number | null;
  error_code?: string | null;
  created_at?: string;
}

export interface AuditLogSummary {
  id: string;
  request_id?: string | null;
  action: string;
  resource_type: string;
  resource_id?: string | null;
  status?: string | null;
  error_code?: string | null;
  service_id?: string | null;
  service_name?: string | null;
  service_slug?: string | null;
  tool_name?: string | null;
  exposed_tool_name?: string | null;
  client_type?: string | null;
  client_name?: string | null;
  created_at?: string;
}

interface ApiFetchOptions extends Omit<RequestInit, 'body' | 'headers'> {
  body?: unknown;
  headers?: HeadersInit;
  token?: string | null;
  auth?: boolean;
}

export function getApiBaseUrl(): string {
  return process.env.NEXT_PUBLIC_COREMCP_API_BASE_URL ?? 'http://localhost:8787';
}

export function getStoredAdminToken(): string | null {
  if (typeof window === 'undefined') return null;
  return window.sessionStorage.getItem(ADMIN_TOKEN_STORAGE_KEY);
}

export function saveAdminToken(token: string): void {
  if (typeof window === 'undefined') return;
  window.sessionStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, token.trim());
}

export function clearAdminToken(): void {
  if (typeof window === 'undefined') return;
  window.sessionStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY);
}

function emitUnauthorized(): void {
  if (typeof window === 'undefined') return;
  clearAdminToken();
  window.dispatchEvent(new CustomEvent('coremcp:unauthorized'));
}

function buildUrl(path: string): string {
  if (/^https?:\/\//.test(path)) return path;
  return new URL(path, getApiBaseUrl()).toString();
}

async function readError(response: Response): Promise<ApiErrorPayload | null> {
  const contentType = response.headers.get('content-type') ?? '';
  if (!contentType.includes('application/json')) return null;

  try {
    return (await response.json()) as ApiErrorPayload;
  } catch {
    return null;
  }
}

export async function apiFetch<T>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  const token = options.token ?? getStoredAdminToken();
  const headers = new Headers(options.headers);
  const shouldAttachAuth = options.auth !== false && Boolean(token);

  headers.set('Accept', 'application/json');
  if (shouldAttachAuth) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  let body: BodyInit | undefined;
  if (options.body !== undefined) {
    headers.set('Content-Type', 'application/json');
    body = JSON.stringify(options.body);
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(path), {
      ...options,
      headers,
      body
    });
  } catch (error) {
    throw new ApiError('CoreMCP API에 연결할 수 없습니다.', {
      code: 'network_error',
      details: error
    });
  }

  if (response.status === 401) {
    emitUnauthorized();
    throw new ApiError('토큰이 만료되었거나 회전되었습니다. 새 토큰을 입력해 주세요.', {
      code: 'unauthorized',
      status: response.status
    });
  }

  if (!response.ok) {
    const payload = await readError(response);
    throw new ApiError(payload?.error?.message ?? `요청에 실패했습니다. 상태 코드: ${response.status}`, {
      code: 'http_error',
      status: response.status,
      requestId: payload?.request_id,
      details: payload?.error?.details
    });
  }

  if (response.status === 204) {
    return undefined as T;
  }

  try {
    return (await response.json()) as T;
  } catch (error) {
    throw new ApiError('응답을 JSON으로 해석할 수 없습니다.', {
      code: 'parse_error',
      status: response.status,
      details: error
    });
  }
}

export const coreMcpApi = {
  health: () => apiFetch<HealthResponse>('/health', { auth: false }),
  settings: () => apiFetch<SettingsResponse>('/v1/settings'),
  listServices: () => apiFetch<ListResponse<McpServiceSummary>>('/v1/mcp-services?limit=20'),
  getService: (serviceId: string) => apiFetch<McpServiceSummary>(`/v1/mcp-services/${serviceId}`),
  createService: (body: {
    name: string;
    slug?: string;
    endpoint_url: string;
    auth_type?: string;
    description?: string;
    category?: string;
    logo_url?: string;
    homepage_url?: string;
    documentation_url?: string;
  }) =>
    apiFetch<McpServiceSummary>('/v1/mcp-services', { method: 'POST', body }),
  updateService: (
    serviceId: string,
    body: Partial<Pick<McpServiceSummary, 'name' | 'slug' | 'description' | 'endpoint_url' | 'auth_type' | 'status' | 'category' | 'logo_url' | 'homepage_url' | 'documentation_url'>>
  ) =>
    apiFetch<McpServiceSummary>(`/v1/mcp-services/${serviceId}`, { method: 'PATCH', body }),
  deleteService: (serviceId: string) =>
    apiFetch<{ id: string; status: string }>(`/v1/mcp-services/${serviceId}`, { method: 'DELETE' }),
  validateService: (serviceId: string) =>
    apiFetch<{ service_id: string; status: string; tools_found: number; job_id: string; warnings?: unknown[] }>(`/v1/mcp-services/${serviceId}/validate`, { method: 'POST' }),
  listServiceTools: (serviceId: string) => apiFetch<ListResponse<ServiceToolSummary>>(`/v1/mcp-services/${serviceId}/tools`),
  listToolOverrides: (serviceId: string) => apiFetch<ListResponse<ToolOverrideSummary>>(`/v1/mcp-services/${serviceId}/tool-overrides`),
  updateToolOverride: (serviceId: string, serviceToolId: string, body: { enabled: boolean; permission_level: ToolPermissionLevel }) =>
    apiFetch<ToolOverrideSummary>(`/v1/mcp-services/${serviceId}/tool-overrides/${serviceToolId}`, { method: 'PUT', body }),
  applyToolPreset: (serviceId: string, preset: ToolPreset) =>
    apiFetch<ToolPresetResponse>(`/v1/mcp-services/${serviceId}/tool-overrides/preset`, { method: 'POST', body: { preset } }),
  getServiceCredential: (serviceId: string) => apiFetch<ServiceCredentialSummary>(`/v1/mcp-services/${serviceId}/credential`),
  putServiceCredential: (serviceId: string, body: { credential_type: string; secret: string; header_name?: string }) =>
    apiFetch<ServiceCredentialSummary>(`/v1/mcp-services/${serviceId}/credential`, { method: 'PUT', body }),
  rotateServiceCredential: (serviceId: string, body: { secret: string; credential_type?: string; header_name?: string }) =>
    apiFetch<ServiceCredentialSummary>(`/v1/mcp-services/${serviceId}/credential/rotate`, { method: 'POST', body }),
  deleteServiceCredential: (serviceId: string) =>
    apiFetch<{ service_id: string; status: string }>(`/v1/mcp-services/${serviceId}/credential`, { method: 'DELETE' }),
  listToolboxes: () => apiFetch<ListResponse<ToolboxSummary>>('/v1/toolboxes?limit=20'),
  getToolbox: (toolboxId: string) => apiFetch<ToolboxSummary>(`/v1/toolboxes/${toolboxId}`),
  addToolboxItem: (toolboxId: string, serviceId: string) =>
    apiFetch<ToolboxItemSummary>(`/v1/toolboxes/${toolboxId}/items`, { method: 'POST', body: { service_id: serviceId, enabled: true } }),
  updateToolboxItem: (toolboxId: string, itemId: string, enabled: boolean) =>
    apiFetch<ToolboxItemSummary>(`/v1/toolboxes/${toolboxId}/items/${itemId}`, { method: 'PATCH', body: { enabled } }),
  removeToolboxItem: (toolboxId: string, itemId: string) =>
    apiFetch<{ id: string; status: string }>(`/v1/toolboxes/${toolboxId}/items/${itemId}`, { method: 'DELETE' }),
  listExternalConnections: () => apiFetch<ListResponse<ExternalConnectionSummary>>('/v1/external-connections?limit=20'),
  createExternalConnection: (body: { client_type: string; client_name: string }) =>
    apiFetch<ExternalConnectionSummary>('/v1/external-connections', { method: 'POST', body }),
  createOneTimeConnectionToken: (body: { client_type: string; requested_scopes?: string[] }) =>
    apiFetch<OneTimeConnectionTokenResponse>('/v1/external-connections/one-time-token', { method: 'POST', body }),
  revokeExternalConnection: (connectionId: string) =>
    apiFetch<{ id: string; status: string }>(`/v1/external-connections/${connectionId}`, { method: 'DELETE' }),
  listClientTokens: () => apiFetch<ListResponse<ClientTokenSummary>>('/v1/settings/client-tokens?limit=20'),
  issueClientToken: (externalConnectionId: string) =>
    apiFetch<IssueClientTokenResponse>('/v1/settings/client-tokens', {
      method: 'POST',
      body: { external_connection_id: externalConnectionId, scopes: ['mcp:tools.read', 'mcp:tools.call'] }
    }),
  revokeClientToken: (tokenId: string) =>
    apiFetch<{ id: string; status: string }>(`/v1/settings/client-tokens/${tokenId}`, { method: 'DELETE' }),
  listPlaygroundTools: () => apiFetch<ListResponse<PlaygroundToolSummary>>('/v1/playground/tools/list?limit=100'),
  callPlaygroundTool: (exposedName: string, args: Record<string, unknown>) =>
    apiFetch<unknown>('/v1/playground/tools/call', { method: 'POST', body: { exposed_name: exposedName, arguments: args } }),
  listToolInvocations: () => apiFetch<ListResponse<ToolInvocationSummary>>('/v1/tool-invocations?limit=10'),
  listAuditLogs: () => apiFetch<ListResponse<AuditLogSummary>>('/v1/audit-logs?limit=10')
};
