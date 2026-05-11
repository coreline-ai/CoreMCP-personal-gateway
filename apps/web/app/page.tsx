'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';
import { ToolIcon } from '@/components/tool-icon';
import {
  ADMIN_TOKEN_STORAGE_KEY,
  ApiError,
  coreMcpApi,
  getApiBaseUrl,
  getStoredAdminToken,
  saveAdminToken,
  clearAdminToken
} from '@/lib/api';

const sections = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'services', label: 'Services' },
  { id: 'toolbox', label: 'Toolbox' },
  { id: 'clients', label: 'Connected Clients' },
  { id: 'settings', label: 'Settings/Tokens' },
  { id: 'playground', label: 'Playground' }
];

const dashboardCards = [
  { title: 'Default Toolbox', value: '—', body: '기본 도구함의 MCP와 도구 수를 한눈에 확인합니다.' },
  { title: 'MCP Services', value: '—', body: 'Active, Error, Disabled 상태를 분리해 보여줄 자리입니다.' },
  { title: 'Recent Tool Calls', value: '—', body: '최근 호출 10건의 상태, latency, 시간을 표시합니다.' },
  { title: 'System Health', value: '확인 전', body: 'API, DB, Vault 상태를 연결 후 점검합니다.' }
];

const validationStages = ['URL safety check', 'HTTP reachability', 'MCP initialize', 'tools/list', 'Tool metadata scan'];

const serviceRows = [
  { label: 'Name + slug', help: 'MCP 이름, slug, risk badge' },
  { label: 'Endpoint URL', help: '긴 URL은 말줄임 처리' },
  { label: 'Status', help: 'Active / Validating / Error / Disabled / Auth required' },
  { label: 'Credential', help: 'Connected / Connection required / Reconnect required' }
];

const clientRows = ['Claude Code (Mac mini)', 'Claude Code (MacBook)', 'OpenClaw', 'Cursor / ChatGPT 옵션'];

function classNames(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(' ');
}

function maskToken(token: string | null) {
  if (!token) return '저장된 admin token 없음';
  if (token.length <= 18) return `${token.slice(0, 8)}••••`;
  return `${token.slice(0, 12)}••••${token.slice(-4)}`;
}

function explainError(error: unknown) {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return '알 수 없는 문제가 발생했습니다.';
}

export default function AdminHomePage() {
  const [token, setToken] = useState<string | null>(null);
  const [tokenInput, setTokenInput] = useState('');
  const [healthMessage, setHealthMessage] = useState('API 상태를 아직 확인하지 않았습니다.');
  const [playgroundMessage, setPlaygroundMessage] = useState('도구 목록을 불러오면 여기에서 직접 호출을 준비할 수 있습니다.');

  useEffect(() => {
    const stored = getStoredAdminToken();
    setToken(stored);
    setTokenInput(stored ?? '');

    const handleUnauthorized = () => {
      setToken(null);
      setTokenInput('');
      setHealthMessage('토큰이 만료되었거나 회전되었습니다. 새 토큰을 입력해 주세요.');
    };

    window.addEventListener('coremcp:unauthorized', handleUnauthorized);
    return () => window.removeEventListener('coremcp:unauthorized', handleUnauthorized);
  }, []);

  const tokenPreview = useMemo(() => maskToken(token), [token]);

  function handleTokenSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextToken = tokenInput.trim();
    if (!nextToken) return;
    saveAdminToken(nextToken);
    setToken(nextToken);
    setHealthMessage('admin token을 저장했습니다. API 호출에 Authorization 헤더가 포함됩니다.');
  }

  function handleTokenClear() {
    clearAdminToken();
    setToken(null);
    setTokenInput('');
    setHealthMessage('저장된 admin token을 삭제했습니다.');
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

  async function handleLoadPlaygroundTools() {
    setPlaygroundMessage('도구 목록을 불러오는 중입니다...');
    try {
      const response = await coreMcpApi.listPlaygroundTools();
      setPlaygroundMessage(`사용 가능한 도구 ${response.items.length}개를 불러왔습니다.`);
    } catch (error) {
      setPlaygroundMessage(explainError(error));
    }
  }

  return (
    <main className="min-h-screen px-5 py-6 text-slate-950 sm:px-8 lg:px-10">
      <div className="mx-auto flex max-w-7xl flex-col gap-6">
        <header className="rounded-[28px] border border-white/80 bg-white/85 p-6 shadow-soft backdrop-blur md:p-8">
          <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
            <div className="max-w-3xl">
              <p className="text-sm font-semibold uppercase tracking-[0.18em] text-brand-700">CoreMCP Web Admin</p>
              <h1 className="mt-3 text-3xl font-semibold tracking-[-0.04em] text-slate-950 sm:text-5xl">
                내 MCP 도구함을 한눈에 관리합니다.
              </h1>
              <p className="mt-4 max-w-2xl text-base leading-7 text-slate-600">
                MCP 등록, 도구함 상태, 연결된 AI client, 호출 기록, token 설정을 한 화면에서 빠르게 확인하는 초기 Admin UI입니다.
              </p>
            </div>

            <form onSubmit={handleTokenSubmit} className="w-full rounded-2xl border border-slate-200 bg-slate-50 p-4 lg:max-w-md">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="text-base font-semibold text-slate-950">Admin token</h2>
                  <p className="mt-1 text-sm text-slate-600">~/.coremcp/admin-token 파일의 값을 입력하세요.</p>
                </div>
                <span className={classNames('rounded-full px-3 py-1 text-xs font-medium', token ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700')}>
                  {token ? '저장됨' : '입력 필요'}
                </span>
              </div>
              <label htmlFor="admin-token" className="mt-4 block text-sm font-medium text-slate-700">
                localStorage key: <code className="rounded bg-white px-1.5 py-0.5 font-mono text-xs">{ADMIN_TOKEN_STORAGE_KEY}</code>
              </label>
              <input
                id="admin-token"
                type="password"
                value={tokenInput}
                onChange={(event) => setTokenInput(event.target.value)}
                placeholder="cmcp_admin_..."
                autoComplete="off"
                className="mt-2 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm shadow-sm transition focus:border-brand-500"
              />
              <p className="mt-2 font-mono text-xs text-slate-500">{tokenPreview}</p>
              <div className="mt-4 flex flex-wrap gap-2">
                <button type="submit" className="rounded-xl bg-brand-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-brand-700">
                  Token 저장
                </button>
                <button type="button" onClick={handleTokenClear} className="rounded-xl border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-100">
                  삭제
                </button>
                <button type="button" onClick={handleHealthCheck} className="rounded-xl border border-brand-100 bg-brand-50 px-4 py-2 text-sm font-semibold text-brand-700 transition hover:bg-brand-100">
                  API 상태 확인
                </button>
              </div>
              <p className="mt-3 text-sm text-slate-600">{healthMessage}</p>
            </form>
          </div>

          <nav aria-label="페이지 섹션" className="mt-6 flex gap-2 overflow-x-auto pb-1">
            {sections.map((section) => (
              <a key={section.id} href={`#${section.id}`} className="whitespace-nowrap rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition hover:border-brand-200 hover:text-brand-700">
                {section.label}
              </a>
            ))}
          </nav>
        </header>

        <section id="dashboard" className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {dashboardCards.map((card) => (
            <article key={card.title} className="rounded-2xl border border-white bg-white p-5 shadow-sm">
              <p className="text-sm font-medium text-slate-500">{card.title}</p>
              <p className="mt-3 text-3xl font-semibold tracking-tight text-slate-950">{card.value}</p>
              <p className="mt-3 text-sm leading-6 text-slate-600">{card.body}</p>
            </article>
          ))}
        </section>

        <section className="grid gap-6 xl:grid-cols-[1.25fr_0.75fr]">
          <article id="services" className="rounded-[24px] border border-white bg-white p-6 shadow-sm">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <p className="text-sm font-semibold text-brand-700">Services</p>
                <h2 className="mt-2 text-2xl font-semibold tracking-tight">MCP 등록과 상태</h2>
                <p className="mt-2 text-sm leading-6 text-slate-600">Remote MCP URL을 등록하고 검증 단계, credential 상태, tool 수를 확인합니다.</p>
              </div>
              <a href="#settings" className="rounded-xl bg-slate-950 px-4 py-2 text-sm font-semibold text-white transition hover:bg-slate-800">
                MCP 추가 준비
              </a>
            </div>

            <div className="mt-6 overflow-hidden rounded-2xl border border-slate-200">
              {serviceRows.map((row, index) => (
                <div key={row.label} className={classNames('grid gap-2 p-4 sm:grid-cols-[180px_1fr]', index !== serviceRows.length - 1 && 'border-b border-slate-200')}>
                  <span className="font-medium text-slate-900">{row.label}</span>
                  <span className="text-sm text-slate-600">{row.help}</span>
                </div>
              ))}
            </div>

            <div className="mt-6 rounded-2xl border border-dashed border-slate-300 bg-slate-50 p-5">
              <h3 className="font-semibold text-slate-900">아직 MCP를 등록하지 않았습니다.</h3>
              <p className="mt-2 text-sm leading-6 text-slate-600">Remote MCP URL을 입력해 첫 MCP를 등록하세요. Validation 진행은 아래 단계로 표시됩니다.</p>
              <ol className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
                {validationStages.map((stage) => (
                  <li key={stage} className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700">
                    {stage}
                  </li>
                ))}
              </ol>
            </div>
          </article>

          <article id="toolbox" className="rounded-[24px] border border-white bg-white p-6 shadow-sm">
            <p className="text-sm font-semibold text-brand-700">Toolbox</p>
            <h2 className="mt-2 text-2xl font-semibold tracking-tight">기본 도구함</h2>
            <p className="mt-2 text-sm leading-6 text-slate-600">도구함에 포함된 MCP와 활성 여부, 노출되는 도구 목록을 관리합니다.</p>

            <div className="mt-6 rounded-2xl border border-dashed border-slate-300 bg-slate-50 p-5">
              <div className="flex items-center gap-3">
                <ToolIcon tool={{ name: 'empty-toolbox', title: '도구함 기본 아이콘', icons: [{ src: '/icon-fallback.png' }] }} />
                <div>
                  <h3 className="font-semibold text-slate-900">도구함이 비어 있습니다.</h3>
                  <p className="mt-1 text-sm text-slate-600">MCP를 추가하면 Claude Code와 ChatGPT에서 바로 사용할 수 있습니다.</p>
                </div>
              </div>
              <button type="button" className="mt-5 rounded-xl border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-100">
                MCP 추가
              </button>
            </div>
          </article>
        </section>

        <section className="grid gap-6 xl:grid-cols-2">
          <article id="clients" className="rounded-[24px] border border-white bg-white p-6 shadow-sm">
            <p className="text-sm font-semibold text-brand-700">Connected Clients</p>
            <h2 className="mt-2 text-2xl font-semibold tracking-tight">연결된 AI client</h2>
            <p className="mt-2 text-sm leading-6 text-slate-600">Claude Code, OpenClaw, Cursor 등 연결별 client token과 마지막 사용 시간을 확인합니다.</p>
            <div className="mt-6 grid gap-3">
              {clientRows.map((client) => (
                <div key={client} className="flex items-center justify-between rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3">
                  <span className="font-medium text-slate-800">{client}</span>
                  <span className="rounded-full bg-white px-3 py-1 text-xs font-medium text-slate-500">연결 대기</span>
                </div>
              ))}
            </div>
            <p className="mt-5 text-sm text-slate-600">아직 연결된 AI client가 없습니다. Claude Code 연결 가이드를 확인하세요.</p>
          </article>

          <article id="settings" className="rounded-[24px] border border-white bg-white p-6 shadow-sm">
            <p className="text-sm font-semibold text-brand-700">Settings / Tokens</p>
            <h2 className="mt-2 text-2xl font-semibold tracking-tight">Token 관리</h2>
            <div className="mt-5 rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-950">
              <strong className="font-semibold">주의:</strong> admin token은 CoreMCP API 전체에 대한 관리자 권한을 부여합니다. 공유 또는 노출되지 않도록 주의하세요.
            </div>
            <div className="mt-5 grid gap-3">
              <div className="rounded-2xl border border-slate-200 p-4">
                <p className="text-sm font-medium text-slate-500">Admin Token</p>
                <p className="mt-2 font-mono text-sm text-slate-800">{tokenPreview}</p>
                <p className="mt-2 text-sm text-slate-600">회전 후에는 새 token으로 다시 로그인합니다.</p>
              </div>
              <div className="rounded-2xl border border-slate-200 p-4">
                <p className="text-sm font-medium text-slate-500">Client Tokens</p>
                <p className="mt-2 text-sm text-slate-600">연결된 AI client마다 별도 token을 발급하고, 필요 시 개별 revoke합니다.</p>
              </div>
            </div>
          </article>
        </section>

        <section id="playground" className="rounded-[24px] border border-white bg-white p-6 shadow-sm">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <p className="text-sm font-semibold text-brand-700">Playground</p>
              <h2 className="mt-2 text-2xl font-semibold tracking-tight">도구 직접 호출 테스트</h2>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">기본 도구함에서 tool을 선택하고 JSON arguments를 입력해 호출 결과를 확인하는 영역입니다.</p>
            </div>
            <button type="button" onClick={handleLoadPlaygroundTools} className="rounded-xl border border-brand-100 bg-brand-50 px-4 py-2 text-sm font-semibold text-brand-700 transition hover:bg-brand-100">
              도구 목록 불러오기
            </button>
          </div>
          <div className="mt-6 grid gap-4 lg:grid-cols-[320px_1fr]">
            <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
              <label htmlFor="tool-select" className="text-sm font-medium text-slate-700">Tool 선택</label>
              <select id="tool-select" className="mt-2 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm">
                <option>도구 목록을 먼저 불러오세요</option>
              </select>
              <p className="mt-4 text-sm text-slate-600">{playgroundMessage}</p>
            </div>
            <div className="rounded-2xl border border-slate-200 p-4">
              <label htmlFor="arguments" className="text-sm font-medium text-slate-700">Arguments JSON</label>
              <textarea
                id="arguments"
                rows={8}
                spellCheck={false}
                defaultValue={'{\n  "example": true\n}'}
                className="mt-2 w-full rounded-xl border border-slate-300 bg-slate-950 px-3 py-3 font-mono text-sm text-slate-100 shadow-inner"
              />
              <div className="mt-4 flex items-center justify-between gap-3">
                <p className="text-sm text-slate-600">응답 JSON, latency, request_id가 이 영역 아래에 표시됩니다.</p>
                <button type="button" className="rounded-xl bg-slate-950 px-4 py-2 text-sm font-semibold text-white transition hover:bg-slate-800">
                  Call tool
                </button>
              </div>
            </div>
          </div>
        </section>

        <footer className="pb-4 text-center text-sm text-slate-500">
          API base: <code className="rounded bg-white/80 px-1.5 py-0.5 font-mono text-xs">{getApiBaseUrl()}</code>
        </footer>
      </div>
    </main>
  );
}
