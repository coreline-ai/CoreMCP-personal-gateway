import { ApiError } from '@/lib/api';

export const sections = [
  { id: 'dashboard', label: 'Dashboard', href: '/', group: 'Gateway' },
  { id: 'logs', label: 'Logs', href: '/logs', group: 'Gateway' },
  { id: 'services', label: 'Services', href: '/services', group: 'MCP' },
  { id: 'toolbox', label: '도구함', href: '/toolbox', group: 'MCP' },
  { id: 'playground', label: 'Playground', href: '/playground', group: 'MCP' },
  { id: 'clients', label: '연결된 AI client', href: '/clients', group: 'Connections' },
  { id: 'settings', label: 'Settings/Tokens', href: '/settings', group: 'Configure' }
] as const;

export const pageTitles: Record<string, { title: string; description: string }> = {
  dashboard: {
    title: 'Dashboard',
    description: 'CoreMCP gateway, 도구함, client token, 최근 호출 상태를 확인합니다.'
  },
  services: {
    title: 'MCP 추가/등록',
    description: 'Remote MCP URL을 등록하고 validation과 catalog cache를 관리합니다.'
  },
  toolbox: {
    title: '도구함',
    description: 'Codex CLI exec와 선택 client에 노출할 default toolbox를 관리합니다.'
  },
  clients: {
    title: '연결된 AI client',
    description: 'Codex CLI exec를 1차 경로로 두고 client token과 연결 상태를 관리합니다.'
  },
  settings: {
    title: 'Settings/Tokens',
    description: 'admin token session 상태와 client token revoke를 관리합니다.'
  },
  playground: {
    title: 'Playground',
    description: '현재 도구함의 tool을 직접 호출해 응답을 확인합니다.'
  },
  logs: {
    title: 'Logs',
    description: 'tool invocation과 audit event를 확인합니다.'
  }
};

export const legacySections = [
  { id: 'dashboard', label: 'Dashboard', href: '/' },
  { id: 'services', label: 'Services', href: '/services' },
  { id: 'toolbox', label: 'Toolbox', href: '/toolbox' },
  { id: 'clients', label: 'Connected Clients', href: '/clients' },
  { id: 'settings', label: 'Settings/Tokens', href: '/settings' },
  { id: 'playground', label: 'Playground', href: '/playground' },
  { id: 'logs', label: 'Logs', href: '/logs' }
] as const;

export const validationStages = ['URL safety check', 'MCP initialize', 'tools/list', 'Tool metadata scan', 'DB catalog update'];

export function classNames(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(' ');
}

export function maskToken(token: string | null) {
  if (!token) return '저장된 admin token 없음';
  if (token.length <= 18) return `${token.slice(0, 8)}••••`;
  return `${token.slice(0, 12)}••••${token.slice(-4)}`;
}

export function explainError(error: unknown) {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return '알 수 없는 문제가 발생했습니다.';
}

export function statusPill(status?: string) {
  if (status === 'active' || status === 'success') return 'bg-emerald-50 text-emerald-700 ring-emerald-200';
  if (status === 'error' || status === 'revoked' || status === 'deleted') return 'bg-rose-50 text-rose-700 ring-rose-200';
  if (status === 'validating') return 'bg-blue-50 text-blue-700 ring-blue-200';
  if (status === 'auth_required' || status === 'not_connected') return 'bg-amber-50 text-amber-700 ring-amber-200';
  return 'bg-slate-50 text-slate-600 ring-slate-200';
}

export function riskPill(risk?: string | null) {
  if (risk === 'dangerous' || risk === 'high' || risk === 'destructive') return 'bg-rose-50 text-rose-700 ring-rose-200';
  if (risk === 'medium' || risk === 'warning') return 'bg-amber-50 text-amber-700 ring-amber-200';
  if (risk === 'low' || risk === 'safe') return 'bg-emerald-50 text-emerald-700 ring-emerald-200';
  return 'bg-slate-50 text-slate-600 ring-slate-200';
}

export function logStatusPill(status?: string | null, errorCode?: string | null) {
  const normalized = status?.toLowerCase();
  if (errorCode || normalized?.includes('error') || normalized?.includes('deny') || normalized === 'failed') {
    return 'bg-rose-50 text-rose-700 ring-rose-200';
  }
  if (normalized?.includes('success') || normalized === 'ok' || normalized === 'completed') {
    return 'bg-emerald-50 text-emerald-700 ring-emerald-200';
  }
  if (normalized?.includes('pending') || normalized?.includes('running')) {
    return 'bg-blue-50 text-blue-700 ring-blue-200';
  }
  return 'bg-slate-50 text-slate-600 ring-slate-200';
}

export function shortRequestId(requestId?: string | null) {
  if (!requestId) return 'request_id 없음';
  if (requestId.length <= 16) return requestId;
  return `${requestId.slice(0, 8)}…${requestId.slice(-6)}`;
}
