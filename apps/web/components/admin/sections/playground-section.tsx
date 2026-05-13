'use client';

import type { PlaygroundToolSummary } from '@/lib/api';

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
  return (
    <section id="playground" className="cm-card">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div><p className="cm-kicker">Playground</p><h2 className="cm-section-title">도구 직접 호출 테스트</h2></div>
        <button type="button" onClick={onLoadPlaygroundTools} className="cm-button cm-button-brand">도구 목록 불러오기</button>
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-[320px_1fr]">
        <div className="cm-panel-subtle">
          <label htmlFor="tool-select" className="text-sm font-medium text-foreground">Tool 선택</label>
          <select id="tool-select" value={selectedTool} onChange={(event) => onSelectedToolChange(event.target.value)} className="mt-2 cm-select">
            <option value="">도구 선택</option>
            {playgroundTools.map((tool) => <option key={tool.name} value={tool.name}>{tool.name}</option>)}
          </select>
          <p className="mt-4 text-sm text-muted-foreground">{playgroundTools.length}개 도구 로드됨</p>
        </div>
        <div className="cm-panel">
          <label htmlFor="arguments" className="text-sm font-medium text-foreground">Arguments JSON</label>
          <textarea id="arguments" rows={8} spellCheck={false} value={argumentsJson} onChange={(event) => onArgumentsJsonChange(event.target.value)} className="mt-2 cm-textarea" />
          <div className="mt-4 flex items-center justify-between gap-3"><p className="text-sm text-muted-foreground">응답 JSON이 아래에 표시됩니다.</p><button type="button" onClick={onCallTool} className="cm-button cm-button-primary">Call tool</button></div>
          <pre className="mt-4 cm-code-block max-h-72">{callResult}</pre>
        </div>
      </div>
    </section>
  );
}
