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
}

export interface ListResponse<T> {
  items: T[];
  next_cursor: string | null;
}

export interface McpServiceSummary {
  id: string;
  name: string;
  slug: string;
  status: 'draft' | 'validating' | 'active' | 'error' | 'disabled' | 'auth_required' | string;
  tool_count?: number;
  last_validated_at?: string | null;
  updated_at?: string | null;
}

export interface ToolboxSummary {
  id: string;
  name: string;
  is_default?: boolean;
  item_count?: number;
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
  return window.localStorage.getItem(ADMIN_TOKEN_STORAGE_KEY);
}

export function saveAdminToken(token: string): void {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, token.trim());
}

export function clearAdminToken(): void {
  if (typeof window === 'undefined') return;
  window.localStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY);
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
  listToolboxes: () => apiFetch<ListResponse<ToolboxSummary>>('/v1/toolboxes?limit=20'),
  listExternalConnections: () => apiFetch<ListResponse<ExternalConnectionSummary>>('/v1/external-connections?limit=20'),
  listPlaygroundTools: () => apiFetch<ListResponse<PlaygroundToolSummary>>('/v1/playground/tools/list?limit=100')
};
