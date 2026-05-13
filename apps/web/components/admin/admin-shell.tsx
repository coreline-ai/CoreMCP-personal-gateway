'use client';

import type { FormEvent, ReactNode } from 'react';
import Link from 'next/link';
import { ADMIN_TOKEN_STORAGE_KEY, getApiBaseUrl } from '@/lib/api';
import { classNames, pageTitles, sections } from './admin-utils';
import { ThemeToggle } from './theme-toggle';

interface AdminShellProps {
  activeSection: string;
  statusMessage: string;
  token: string | null;
  tokenInput: string;
  tokenPreview: string;
  healthMessage: string;
  onTokenInputChange: (value: string) => void;
  onTokenSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onTokenClear: () => void;
  onHealthCheck: () => void;
  onRefresh: () => void;
  children: ReactNode;
}

export function AdminShell({
  activeSection,
  statusMessage,
  token,
  tokenInput,
  tokenPreview,
  healthMessage,
  onTokenInputChange,
  onTokenSubmit,
  onTokenClear,
  onHealthCheck,
  onRefresh,
  children
}: AdminShellProps) {
  const page = pageTitles[activeSection] ?? pageTitles.dashboard;
  const groups = ['Gateway', 'MCP', 'Connections', 'Configure'] as const;

  return (
    <main className="cm-app-shell">
      <aside className="cm-sidebar" aria-label="CoreMCP navigation">
        <div className="cm-sidebar-header">
          <Link href="/" className="flex items-center gap-2 rounded-lg px-1 py-1 transition-colors hover:bg-muted">
            <span className="grid size-8 place-items-center rounded-lg bg-primary font-mono text-xs font-medium text-primary-foreground">CM</span>
            <span className="min-w-0">
              <span className="block truncate text-sm font-medium">CoreMCP</span>
              <span className="block truncate font-mono text-xs text-muted-foreground">{getApiBaseUrl()}</span>
            </span>
          </Link>
          <div className="flex items-center justify-between gap-2 px-1">
            <span className="text-xs text-muted-foreground">Local Gateway</span>
            <span className={classNames('rounded-full px-2 py-0.5 text-xs font-medium', token ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700')}>
              {token ? 'auth ok' : 'token 필요'}
            </span>
          </div>
          <Link href="/services" className="cm-sidebar-link" data-active={activeSection === 'services' ? 'true' : 'false'}>
            <span aria-hidden="true">＋</span>
            <span>MCP 추가/등록</span>
          </Link>
        </div>

        <nav className="cm-sidebar-content" aria-label="CoreMCP menu">
          {groups.map((group) => (
            <div key={group} className="cm-sidebar-group">
              <p className="cm-sidebar-label">{group}</p>
              <div className="grid gap-0.5">
                {sections.filter((section) => section.group === group).map((section) => (
                  <Link key={section.id} href={section.href} className="cm-sidebar-link" data-active={activeSection === section.id ? 'true' : 'false'}>
                    <span className="size-1.5 rounded-full bg-current opacity-45" aria-hidden="true" />
                    <span className="truncate">{section.label}</span>
                  </Link>
                ))}
              </div>
            </div>
          ))}
        </nav>

        <form onSubmit={onTokenSubmit} className="cm-sidebar-footer">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-sm font-medium text-foreground">Admin token</h2>
            <button type="button" onClick={onHealthCheck} className="cm-button cm-button-secondary cm-button-sm">Health</button>
          </div>
          <label htmlFor="admin-token" className="mt-3 block text-xs font-medium text-muted-foreground">
            <code className="rounded bg-card px-1.5 py-0.5 font-mono text-xs ring-1 ring-border">{ADMIN_TOKEN_STORAGE_KEY}</code>
          </label>
          <input
            id="admin-token"
            type="password"
            value={tokenInput}
            onChange={(event) => onTokenInputChange(event.target.value)}
            placeholder="cmcp_admin_..."
            autoComplete="off"
            className="mt-2 cm-input"
          />
          <p className="mt-2 truncate font-mono text-xs text-muted-foreground">{tokenPreview}</p>
          <div className="mt-3 flex gap-2">
            <button type="submit" className="cm-button cm-button-primary cm-button-sm">저장</button>
            <button type="button" onClick={onTokenClear} className="cm-button cm-button-secondary cm-button-sm">삭제</button>
          </div>
          <ThemeToggle />
          <p className="mt-3 line-clamp-2 text-xs leading-5 text-muted-foreground">{healthMessage}</p>
        </form>
      </aside>

      <section className="cm-page-frame">
        <header className="cm-page-header">
          <div className="min-w-0">
            <div className="flex items-center gap-2 md:hidden">
              <span className="grid size-7 place-items-center rounded-lg bg-primary font-mono text-[0.7rem] font-medium text-primary-foreground">CM</span>
              <span className="text-xs text-muted-foreground">CoreMCP</span>
            </div>
            <h1 className="truncate text-sm font-medium text-foreground">{page.title}</h1>
            <p className="hidden truncate text-xs text-muted-foreground sm:block">{page.description}</p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <span className={classNames('hidden rounded-full px-2 py-0.5 text-xs font-medium sm:inline-flex', token ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700')}>
              {token ? 'session 저장됨' : 'token 필요'}
            </span>
            <button type="button" onClick={onRefresh} className="cm-button cm-button-secondary cm-button-sm">새로고침</button>
          </div>
        </header>

        <nav aria-label="모바일 페이지 메뉴" className="flex gap-1 overflow-x-auto border-b border-border px-3 py-2 md:hidden">
          {sections.map((section) => (
            <Link key={section.id} href={section.href} className={classNames('whitespace-nowrap rounded-lg px-2.5 py-1.5 text-sm transition-colors hover:bg-muted hover:text-foreground', activeSection === section.id ? 'bg-muted font-medium text-foreground' : 'text-muted-foreground')}>
              {section.label}
            </Link>
          ))}
        </nav>

        <div className="cm-page-content">
          <div className="mx-auto grid max-w-7xl gap-4">
            <p className="cm-status-banner">{statusMessage}</p>
            {children}
          </div>
        </div>
      </section>
    </main>
  );
}
