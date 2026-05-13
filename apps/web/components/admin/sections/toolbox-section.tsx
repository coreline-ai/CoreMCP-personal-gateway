'use client';

import { ToolIcon } from '@/components/tool-icon';
import type { ToolOverrideSummary, ToolboxItemSummary, ToolboxSummary } from '@/lib/api';

interface ToolboxSectionProps {
  toolboxes: ToolboxSummary[];
  toolboxItems: ToolboxItemSummary[];
  toolOverridesByService?: Record<string, ToolOverrideSummary[]>;
  onToggleToolboxItem: (item: ToolboxItemSummary) => void;
  onRemoveToolboxItem: (item: ToolboxItemSummary) => void;
}

function countOverrides(overrides: ToolOverrideSummary[]) {
  return overrides.reduce(
    (summary, override) => {
      if (!override.enabled) summary.disabled += 1;
      else if (override.permission_level === 'hidden') summary.hidden += 1;
      else if (override.permission_level === 'visible_only') summary.visibleOnly += 1;
      else summary.callable += 1;
      return summary;
    },
    { callable: 0, visibleOnly: 0, hidden: 0, disabled: 0 }
  );
}

export function ToolboxSection({ toolboxes, toolboxItems, toolOverridesByService = {}, onToggleToolboxItem, onRemoveToolboxItem }: ToolboxSectionProps) {
  return (
    <article id="toolbox" className="cm-card">
      <p className="cm-kicker">Toolbox</p>
      <h2 className="cm-section-title">기본 도구함</h2>
      <p className="cm-copy">{toolboxes.length}개 toolbox 중 기본 도구함 상태입니다. service-level enable과 tool-level permission을 분리해 확인합니다.</p>
      <div className="mt-4 grid gap-3">
        {toolboxItems.length === 0 ? (
          <div className="cm-empty">
            <div className="flex items-center gap-3">
              <ToolIcon tool={{ name: 'empty-toolbox', title: '도구함 기본 아이콘', icons: [{ src: '/icon-fallback.png' }] }} />
              <div>
                <h3 className="font-medium text-foreground">도구함이 비어 있습니다.</h3>
                <p className="mt-1 text-sm text-muted-foreground">Fake MCP 추가 → Validate 실행 → 도구함 추가 순서로 시작하세요.</p>
              </div>
            </div>
          </div>
        ) : toolboxItems.map((item) => {
          const overrides = toolOverridesByService[item.service_id] ?? [];
          const counted = countOverrides(overrides);
          const fallbackCounts = {
            callable: item.callable_tool_count ?? counted.callable,
            visibleOnly: item.visible_only_tool_count ?? counted.visibleOnly,
            hidden: item.hidden_tool_count ?? counted.hidden,
            disabled: item.disabled_tool_count ?? counted.disabled
          };
          const hasToolLevelState = overrides.length > 0
            || item.callable_tool_count != null
            || item.visible_only_tool_count != null
            || item.hidden_tool_count != null
            || item.disabled_tool_count != null;

          return (
            <div key={item.id} className="cm-panel-subtle">
              <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-medium text-foreground">{item.service_name}</p>
                    <span className={item.enabled ? 'rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700 ring-1 ring-emerald-200' : 'rounded-full bg-muted px-2.5 py-1 text-xs font-medium text-muted-foreground ring-1 ring-border'}>
                      service {item.enabled ? 'enabled' : 'disabled'}
                    </span>
                    {item.service_status && <span className="rounded-full bg-card px-2.5 py-1 text-xs font-medium text-muted-foreground ring-1 ring-border">{item.service_status}</span>}
                  </div>
                  <p className="mt-1 text-sm text-muted-foreground">{item.service_slug} · {item.tool_count ?? 0} cached tools</p>
                  <div className="mt-3 flex flex-wrap gap-2 text-xs">
                    {hasToolLevelState ? (
                      <>
                        <span className="rounded-full bg-emerald-50 px-2.5 py-1 font-medium text-emerald-700 ring-1 ring-emerald-200">callable {fallbackCounts.callable}</span>
                        <span className="rounded-full bg-amber-50 px-2.5 py-1 font-medium text-amber-700 ring-1 ring-amber-200">visible_only {fallbackCounts.visibleOnly}</span>
                        <span className="rounded-full bg-muted px-2.5 py-1 font-medium text-muted-foreground ring-1 ring-border">hidden {fallbackCounts.hidden}</span>
                        <span className="rounded-full bg-rose-50 px-2.5 py-1 font-medium text-rose-700 ring-1 ring-rose-200">disabled tools {fallbackCounts.disabled}</span>
                      </>
                    ) : (
                      <span className="rounded-full bg-card px-2.5 py-1 font-medium text-muted-foreground ring-1 ring-border">tool-level override 없음 · Detail Tools 탭에서 설정</span>
                    )}
                  </div>
                </div>
                <div className="flex gap-2">
                  <button type="button" onClick={() => onToggleToolboxItem(item)} className="cm-button cm-button-secondary cm-button-sm">
                    {item.enabled ? 'Disable service' : 'Enable service'}
                  </button>
                  <button type="button" onClick={() => onRemoveToolboxItem(item)} className="cm-button cm-button-danger cm-button-sm">
                    Remove
                  </button>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </article>
  );
}
