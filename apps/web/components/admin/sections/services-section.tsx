'use client';

import type { FormEvent } from 'react';
import { useMemo, useState } from 'react';
import type { McpServiceSummary } from '@/lib/api';
import { classNames, statusPill, validationStages } from '../admin-utils';

interface ServicesSectionProps {
  services: McpServiceSummary[];
  serviceName: string;
  serviceSlug: string;
  serviceUrl: string;
  onServiceNameChange: (value: string) => void;
  onServiceSlugChange: (value: string) => void;
  onServiceUrlChange: (value: string) => void;
  onCreateService: (event: FormEvent<HTMLFormElement>) => void;
  onValidate: (serviceId: string) => void;
  onAddToToolbox: (serviceId: string) => void;
}

export function ServicesSection({
  services,
  serviceName,
  serviceSlug,
  serviceUrl,
  onServiceNameChange,
  onServiceSlugChange,
  onServiceUrlChange,
  onCreateService,
  onValidate,
  onAddToToolbox
}: ServicesSectionProps) {
  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [sortMode, setSortMode] = useState<'recent' | 'name' | 'tools'>('recent');

  const statusOptions = useMemo(() => Array.from(new Set(services.map((service) => service.status).filter(Boolean))).sort(), [services]);
  const filteredServices = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    const items = services.filter((service) => {
      const matchesStatus = statusFilter === 'all' || service.status === statusFilter;
      const searchText = [
        service.name,
        service.slug,
        service.endpoint_url,
        service.description,
        service.category
      ].filter(Boolean).join(' ').toLowerCase();
      return matchesStatus && (!normalizedQuery || searchText.includes(normalizedQuery));
    });
    return [...items].sort((left, right) => {
      if (sortMode === 'name') return left.name.localeCompare(right.name);
      if (sortMode === 'tools') return (right.tool_count ?? 0) - (left.tool_count ?? 0);
      return String(right.updated_at ?? '').localeCompare(String(left.updated_at ?? ''));
    });
  }, [query, services, sortMode, statusFilter]);

  return (
    <article id="services" className="cm-card">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="cm-kicker">Services</p>
          <h2 className="cm-section-title">MCP 추가/등록과 validation</h2>
          <p className="cm-copy">Remote MCP URL을 등록하고 DB catalog로 캐시합니다.</p>
        </div>
      </div>

      <form onSubmit={onCreateService} className="mt-4 grid gap-3 cm-panel-subtle lg:grid-cols-[1fr_0.7fr_1.5fr_auto]">
        <input value={serviceName} onChange={(event) => onServiceNameChange(event.target.value)} className="cm-input py-2" placeholder="Service name" />
        <input value={serviceSlug} onChange={(event) => onServiceSlugChange(event.target.value)} className="cm-input py-2" placeholder="slug" />
        <input value={serviceUrl} onChange={(event) => onServiceUrlChange(event.target.value)} className="cm-input py-2" placeholder="https://.../mcp" />
        <button type="submit" className="cm-button cm-button-primary">등록</button>
      </form>

      <div className="mt-4 grid gap-3 rounded-2xl border border-border bg-card/80 p-3 lg:grid-cols-[1fr_180px_180px]">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="cm-input py-2"
          placeholder="서비스명, slug, URL, category 검색"
        />
        <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} className="cm-select">
          <option value="all">모든 상태</option>
          {statusOptions.map((status) => <option key={status} value={status}>{status}</option>)}
        </select>
        <select value={sortMode} onChange={(event) => setSortMode(event.target.value as 'recent' | 'name' | 'tools')} className="cm-select">
          <option value="recent">최근 업데이트순</option>
          <option value="name">이름순</option>
          <option value="tools">도구 많은순</option>
        </select>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">표시 {filteredServices.length}개 / 전체 {services.length}개</p>

      <div className="mt-4 grid gap-3">
        {services.length === 0 ? (
          <div className="cm-empty">
            <h3 className="font-medium text-foreground">아직 MCP를 등록하지 않았습니다.</h3>
            <ol className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
              {validationStages.map((stage) => <li key={stage} className="rounded-lg border border-border bg-card px-3 py-2 text-xs font-medium text-muted-foreground">{stage}</li>)}
            </ol>
          </div>
        ) : filteredServices.length === 0 ? (
          <div className="cm-empty">
            <h3 className="font-medium text-foreground">검색 조건에 맞는 MCP가 없습니다.</h3>
            <p className="mt-2 text-sm text-muted-foreground">검색어 또는 상태 필터를 조정하세요.</p>
          </div>
        ) : filteredServices.map((service) => (
          <div key={service.id} className="cm-panel">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <a href={`/services/${service.id}`} className="font-medium text-foreground transition hover:underline">{service.name}</a>
                  <span className={classNames('rounded-full px-2.5 py-1 text-xs font-medium ring-1', statusPill(service.status))}>{service.status}</span>
                  <span className="rounded-full bg-muted px-2.5 py-1 text-xs font-medium text-muted-foreground">tools {service.tool_count ?? 0}</span>
                </div>
                <p className="mt-1 font-mono text-xs text-muted-foreground">{service.slug} · {service.endpoint_url}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <a href={`/services/${service.id}`} className="cm-button cm-button-secondary">Detail</a>
                <button type="button" onClick={() => onValidate(service.id)} className="cm-button cm-button-brand">Validate</button>
                <button type="button" onClick={() => onAddToToolbox(service.id)} className="cm-button cm-button-secondary">도구함 추가</button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </article>
  );
}
