'use client';

import { useMemo, useState } from 'react';
import type { PlaygroundToolSummary } from '@/lib/api';
import { classNames } from '../admin-utils';

interface PlaygroundSectionProps {
  playgroundTools: PlaygroundToolSummary[];
  selectedTool: string;
  argumentsJson: string;
  callResult: string;
  onLoadPlaygroundTools: () => void;
  onSelectedToolChange: (value: string) => void;
  onArgumentsJsonChange: (value: string) => void;
  onCallTool: () => void;
}

interface SchemaProperty {
  key: string;
  type: string;
  description?: string;
  required: boolean;
  enumValues?: string[];
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function schemaProperties(tool: PlaygroundToolSummary | undefined): SchemaProperty[] {
  const schema = asRecord(tool?.inputSchema) ?? asRecord(tool?.input_schema);
  const properties = asRecord(schema?.properties);
  if (!properties) return [];
  const required = Array.isArray(schema?.required) ? new Set(schema.required.filter((item): item is string => typeof item === 'string')) : new Set<string>();
  return Object.entries(properties).map(([key, raw]) => {
    const property = asRecord(raw) ?? {};
    const enumValues = Array.isArray(property.enum)
      ? property.enum.filter((item): item is string => typeof item === 'string')
      : undefined;
    return {
      key,
      type: typeof property.type === 'string' ? property.type : 'string',
      description: typeof property.description === 'string' ? property.description : undefined,
      required: required.has(key),
      enumValues
    };
  });
}

function parseArguments(raw: string): Record<string, unknown> {
  const parsed = JSON.parse(raw || '{}') as unknown;
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return {};
  }
  return parsed as Record<string, unknown>;
}

function formatValueForInput(value: unknown): string {
  if (value === undefined || value === null) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return JSON.stringify(value);
}

function diffSummary(previousResult: string, currentResult: string): string {
  if (!previousResult || previousResult === currentResult) return '이전 결과와 동일합니다.';
  return `이전 ${previousResult.length.toLocaleString()}자 → 현재 ${currentResult.length.toLocaleString()}자`;
}

export function PlaygroundSection({
  playgroundTools,
  selectedTool,
  argumentsJson,
  callResult,
  onLoadPlaygroundTools,
  onSelectedToolChange,
  onArgumentsJsonChange,
  onCallTool
}: PlaygroundSectionProps) {
  const [favorites, setFavorites] = useState<string[]>([]);
  const [previousResult, setPreviousResult] = useState('');
  const [manualMode, setManualMode] = useState(false);

  const orderedTools = useMemo(() => {
    return [...playgroundTools].sort((left, right) => {
      const leftPinned = favorites.includes(left.name);
      const rightPinned = favorites.includes(right.name);
      if (leftPinned !== rightPinned) return leftPinned ? -1 : 1;
      return left.name.localeCompare(right.name);
    });
  }, [favorites, playgroundTools]);

  const selectedToolSummary = useMemo(
    () => playgroundTools.find((tool) => tool.name === selectedTool),
    [playgroundTools, selectedTool]
  );
  const properties = useMemo(() => schemaProperties(selectedToolSummary), [selectedToolSummary]);
  const parsedArguments = useMemo(() => {
    try {
      return parseArguments(argumentsJson);
    } catch {
      return {};
    }
  }, [argumentsJson]);
  const argumentParseError = useMemo(() => {
    try {
      parseArguments(argumentsJson);
      return null;
    } catch (error) {
      return error instanceof Error ? error.message : 'JSON parse error';
    }
  }, [argumentsJson]);

  function updateArgument(property: SchemaProperty, rawValue: string, checked?: boolean) {
    const next = { ...parsedArguments };
    if (property.type === 'boolean') {
      next[property.key] = Boolean(checked);
    } else if (property.type === 'number' || property.type === 'integer') {
      next[property.key] = rawValue === '' ? null : Number(rawValue);
    } else {
      next[property.key] = rawValue;
    }
    onArgumentsJsonChange(JSON.stringify(next, null, 2));
  }

  function toggleFavorite(toolName: string) {
    setFavorites((current) => current.includes(toolName)
      ? current.filter((item) => item !== toolName)
      : [...current, toolName]);
  }

  function callWithReplaySnapshot() {
    if (callResult && !callResult.includes('도구를 호출하는 중입니다')) {
      setPreviousResult(callResult);
    }
    onCallTool();
  }

  return (
    <section id="playground" className="cm-card">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <p className="cm-kicker">Playground</p>
          <h2 className="cm-section-title">도구 직접 호출 테스트</h2>
          <p className="cm-copy">inputSchema 기반 폼, 수동 JSON, replay, 결과 diff를 한 화면에서 확인합니다.</p>
        </div>
        <button type="button" onClick={onLoadPlaygroundTools} className="cm-button cm-button-brand">도구 목록 불러오기</button>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-[340px_1fr]">
        <div className="cm-panel-subtle">
          <label htmlFor="tool-select" className="text-sm font-medium text-foreground">Tool 선택</label>
          <select id="tool-select" value={selectedTool} onChange={(event) => onSelectedToolChange(event.target.value)} className="mt-2 cm-select">
            <option value="">도구 선택</option>
            {orderedTools.map((tool) => (
              <option key={tool.name} value={tool.name}>
                {favorites.includes(tool.name) ? '★ ' : ''}{tool.name}
              </option>
            ))}
          </select>
          <p className="mt-4 text-sm text-muted-foreground">{playgroundTools.length}개 도구 로드됨 · 즐겨찾기 {favorites.length}개</p>
          {selectedToolSummary ? (
            <div className="mt-4 rounded-2xl border border-border bg-card p-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold text-foreground">{selectedToolSummary.title || selectedToolSummary.name}</p>
                  <p className="mt-1 text-xs text-muted-foreground">{selectedToolSummary.description || '설명 없음'}</p>
                </div>
                <button
                  type="button"
                  onClick={() => toggleFavorite(selectedToolSummary.name)}
                  className={classNames('rounded-full px-2.5 py-1 text-xs font-medium ring-1', favorites.includes(selectedToolSummary.name) ? 'bg-brand-100 text-brand-700 ring-brand-200 dark:bg-brand-500/15 dark:text-brand-200 dark:ring-brand-400/30' : 'bg-muted text-muted-foreground ring-border')}
                >
                  {favorites.includes(selectedToolSummary.name) ? '★ Pinned' : '☆ Pin'}
                </button>
              </div>
            </div>
          ) : null}
        </div>

        <div className="grid gap-4">
          {properties.length > 0 && !manualMode ? (
            <div className="cm-panel">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="text-sm font-semibold text-foreground">Schema form</p>
                  <p className="text-xs text-muted-foreground">간단한 primitive 입력은 폼으로 수정하고, 복잡한 구조는 JSON 모드로 전환하세요.</p>
                </div>
                <button type="button" onClick={() => setManualMode(true)} className="cm-button cm-button-secondary">JSON 직접 편집</button>
              </div>
              <div className="mt-4 grid gap-3">
                {properties.map((property) => {
                  const value = parsedArguments[property.key];
                  if (property.enumValues?.length) {
                    return (
                      <label key={property.key} className="grid gap-1 text-sm">
                        <span className="font-medium text-foreground">{property.key}{property.required ? ' *' : ''}</span>
                        <select value={formatValueForInput(value)} onChange={(event) => updateArgument(property, event.target.value)} className="cm-select">
                          <option value="">선택</option>
                          {property.enumValues.map((item) => <option key={item} value={item}>{item}</option>)}
                        </select>
                        {property.description ? <span className="text-xs text-muted-foreground">{property.description}</span> : null}
                      </label>
                    );
                  }
                  if (property.type === 'boolean') {
                    return (
                      <label key={property.key} className="flex items-center gap-3 rounded-xl border border-border bg-card px-3 py-2 text-sm">
                        <input type="checkbox" checked={Boolean(value)} onChange={(event) => updateArgument(property, '', event.target.checked)} />
                        <span className="font-medium text-foreground">{property.key}{property.required ? ' *' : ''}</span>
                        {property.description ? <span className="text-xs text-muted-foreground">{property.description}</span> : null}
                      </label>
                    );
                  }
                  return (
                    <label key={property.key} className="grid gap-1 text-sm">
                      <span className="font-medium text-foreground">{property.key}{property.required ? ' *' : ''}</span>
                      <input
                        value={formatValueForInput(value)}
                        type={property.type === 'number' || property.type === 'integer' ? 'number' : 'text'}
                        onChange={(event) => updateArgument(property, event.target.value)}
                        className="cm-input py-2"
                      />
                      {property.description ? <span className="text-xs text-muted-foreground">{property.description}</span> : null}
                    </label>
                  );
                })}
              </div>
            </div>
          ) : (
            <div className="cm-panel">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <label htmlFor="arguments" className="text-sm font-medium text-foreground">Arguments JSON</label>
                {properties.length > 0 ? <button type="button" onClick={() => setManualMode(false)} className="cm-button cm-button-secondary">Schema form으로 전환</button> : null}
              </div>
              <textarea id="arguments" rows={8} spellCheck={false} value={argumentsJson} onChange={(event) => onArgumentsJsonChange(event.target.value)} className="mt-2 cm-textarea" />
              {argumentParseError ? <p className="mt-2 text-xs font-medium text-red-500">JSON 오류: {argumentParseError}</p> : null}
            </div>
          )}

          <div className="cm-panel">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="text-sm text-muted-foreground">Replay는 같은 도구/arguments로 재호출합니다.</p>
              <div className="flex flex-wrap gap-2">
                <button type="button" onClick={callWithReplaySnapshot} className="cm-button cm-button-primary">Call tool</button>
                <button type="button" onClick={callWithReplaySnapshot} disabled={!selectedTool} className="cm-button cm-button-secondary">Replay</button>
              </div>
            </div>
            <pre className="mt-4 cm-code-block max-h-72">{callResult}</pre>
            {previousResult ? (
              <details className="mt-4 rounded-2xl border border-border bg-card/80 p-3">
                <summary className="cursor-pointer text-sm font-semibold text-foreground">결과 diff · {diffSummary(previousResult, callResult)}</summary>
                <div className="mt-3 grid gap-3 lg:grid-cols-2">
                  <pre className="cm-code-block max-h-56">{previousResult}</pre>
                  <pre className="cm-code-block max-h-56">{callResult}</pre>
                </div>
              </details>
            ) : null}
          </div>
        </div>
      </div>
    </section>
  );
}
