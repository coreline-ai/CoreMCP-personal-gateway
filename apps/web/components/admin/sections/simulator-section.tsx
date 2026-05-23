'use client';

import { useMemo, useReducer } from 'react';
import { coreMcpApi, type CodexSimulatorResponse } from '@/lib/api';
import { classNames, explainError, logStatusPill } from '../admin-utils';

interface SimulatorSectionProps {
  token: string | null;
}

const PRESETS = [
  {
    label: '전체 프로젝트 정리',
    prompt: `project_docs.project_list 도구만 1회 사용해서 전체 프로젝트 목록을 확인해줘. 추가 문서 읽기는 금지.

답변은 아래 형식으로 간결하게 정리해줘.

## 전체 요약
- 총 프로젝트 수, README 있는/없는 수, Markdown 수 기준 문서화 상태를 3줄로 요약

## 카테고리별 분류
| 카테고리 | 대표 프로젝트 | 포함 프로젝트 | 근거 |
|---|---|---|---|

규칙:
- 카테고리는 최대 6개로 묶기
- 포함 프로젝트는 너무 길면 대표 5개 + "외 N개"로 줄이기
- 근거는 "프로젝트명 기준 추정", "README 있음/없음", "MD N개"처럼 짧게 쓰기
- 마지막에 "주의: 본문은 읽지 않고 project_list 메타데이터만 사용" 한 줄 추가
- 전체 답변은 900자 이내`
  },
  {
    label: '대표 README 요약',
    prompt: 'project_docs.project_list 도구로 Markdown 문서가 많은 대표 프로젝트 3개를 고르고, 필요하면 project_docs.project_summary 도구로 README를 확인해서 각 프로젝트 목적을 2줄씩 요약해줘.'
  },
  {
    label: 'MCP 문서 검색',
    prompt: "project_docs.project_docs_search 도구로 'MCP'를 검색하고 관련 문서 후보 5개를 프로젝트명/파일명/짧은 근거 중심으로 정리해줘."
  },
  {
    label: '도구함 확인',
    prompt: 'CoreMCP MCP 도구 목록을 확인하고, 실사용 가능한 도구를 카테고리별로 짧게 정리해줘.'
  }
];

const DEFAULT_PROMPT = PRESETS[0].prompt;

interface SimulatorState {
  prompt: string;
  timeoutSeconds: number;
  result: CodexSimulatorResponse | null;
  errorMessage: string;
  isRunning: boolean;
}

type SimulatorAction =
  | { type: 'SET_PROMPT'; payload: string }
  | { type: 'SET_TIMEOUT'; payload: number }
  | { type: 'RUN_START' }
  | { type: 'RUN_SUCCESS'; payload: CodexSimulatorResponse }
  | { type: 'RUN_FAIL'; payload: string };

const initialSimulatorState: SimulatorState = {
  prompt: DEFAULT_PROMPT,
  timeoutSeconds: 120,
  result: null,
  errorMessage: '',
  isRunning: false
};

function simulatorReducer(state: SimulatorState, action: SimulatorAction): SimulatorState {
  switch (action.type) {
    case 'SET_PROMPT':
      return { ...state, prompt: action.payload };
    case 'SET_TIMEOUT':
      return { ...state, timeoutSeconds: action.payload };
    case 'RUN_START':
      return { ...state, isRunning: true, errorMessage: '', result: null };
    case 'RUN_SUCCESS':
      return { ...state, isRunning: false, result: action.payload };
    case 'RUN_FAIL':
      return { ...state, isRunning: false, errorMessage: action.payload };
  }
}

function formatDuration(durationMs?: number) {
  if (typeof durationMs !== 'number') return '-';
  if (durationMs < 1000) return `${durationMs}ms`;
  return `${(durationMs / 1000).toFixed(1)}s`;
}

export function SimulatorSection({ token }: SimulatorSectionProps) {
  const [state, dispatch] = useReducer(simulatorReducer, initialSimulatorState);
  const { prompt, timeoutSeconds, result, errorMessage, isRunning } = state;
  const setPrompt = (value: string) => dispatch({ type: 'SET_PROMPT', payload: value });
  const setTimeoutSeconds = (value: number) => dispatch({ type: 'SET_TIMEOUT', payload: value });
  const canRun = Boolean(token) && prompt.trim().length > 0 && !isRunning;

  const answer = useMemo(() => result?.answer?.trim() || result?.stdout?.trim() || '', [result]);

  async function runSimulator() {
    if (!canRun) return;
    dispatch({ type: 'RUN_START' });
    try {
      const response = await coreMcpApi.runCodexSimulator(prompt, timeoutSeconds);
      dispatch({ type: 'RUN_SUCCESS', payload: response });
    } catch (error) {
      dispatch({ type: 'RUN_FAIL', payload: explainError(error) });
    }
  }

  return (
    <section id="simulator" className="grid gap-4">
      <div className="cm-card">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <p className="cm-kicker">AI Client Simulator</p>
            <h2 className="cm-section-title">Codex가 CoreMCP 도구를 쓰는 흐름 보기</h2>
            <p className="cm-copy">
              CoreMCP가 LLM이 되는 것이 아니라, 연결된 Codex CLI exec가 `/mcp` 도구함을 실제로 호출하는 과정을 관리자 화면에서 확인합니다.
            </p>
          </div>
          <span className={classNames('w-fit rounded-full px-3 py-1 text-xs font-medium ring-1', token ? 'bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-500/15 dark:text-emerald-200 dark:ring-emerald-400/30' : 'bg-amber-50 text-amber-700 ring-amber-200 dark:bg-amber-500/15 dark:text-amber-200 dark:ring-amber-400/30')}>
            {token ? 'admin session ready' : 'admin token 필요'}
          </span>
        </div>

        <div className="mt-5 grid gap-3">
          <div className="flex flex-wrap gap-2">
            {PRESETS.map((preset) => (
              <button
                key={preset.label}
                type="button"
                onClick={() => setPrompt(preset.prompt)}
                className="cm-button cm-button-secondary cm-button-sm"
              >
                {preset.label}
              </button>
            ))}
          </div>
          <label htmlFor="codex-simulator-prompt" className="text-sm font-medium text-foreground">Prompt</label>
          <textarea
            id="codex-simulator-prompt"
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            rows={7}
            className="cm-textarea font-mono text-sm"
            placeholder="Codex에게 요청할 내용을 입력하세요."
          />
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <label className="grid gap-1 text-sm sm:w-44">
              <span className="font-medium text-foreground">Timeout</span>
              <select value={timeoutSeconds} onChange={(event) => setTimeoutSeconds(Number(event.target.value))} className="cm-select">
                <option value={60}>60초</option>
                <option value={120}>120초</option>
              </select>
            </label>
            <button
              type="button"
              onClick={runSimulator}
              disabled={!canRun}
              className={classNames('cm-button cm-button-brand', !canRun && 'cursor-not-allowed opacity-50')}
            >
              {isRunning ? 'Codex 실행 중...' : 'Codex 시뮬레이션 실행'}
            </button>
          </div>
          {!token ? (
            <p className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-400/30 dark:bg-amber-500/10 dark:text-amber-100">
              왼쪽 사이드바에 admin token을 저장한 뒤 실행할 수 있습니다.
            </p>
          ) : null}
          {errorMessage ? (
            <p className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-400/30 dark:bg-rose-500/10 dark:text-rose-100">{errorMessage}</p>
          ) : null}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
        <div className="cm-card">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="cm-kicker">Chat result</p>
              <h3 className="text-base font-semibold text-foreground">Codex 최종 답변</h3>
            </div>
            {result ? (
              <span className={classNames('rounded-full px-2.5 py-1 text-xs font-medium ring-1', logStatusPill(result.status))}>{result.status}</span>
            ) : null}
          </div>
          <pre className="mt-4 min-h-56 whitespace-pre-wrap rounded-2xl border border-border bg-muted/40 p-4 text-sm leading-6 text-foreground">
            {isRunning ? 'Codex CLI exec를 실행하고 있습니다...' : answer || '아직 실행 결과가 없습니다.'}
          </pre>
        </div>

        <aside className="grid gap-4">
          <div className="cm-card">
            <p className="cm-kicker">Tool trace</p>
            <h3 className="text-base font-semibold text-foreground">MCP 호출 흐름</h3>
            <div className="mt-4 grid gap-2">
              {result?.tool_calls?.length ? result.tool_calls.map((call, index) => (
                <div key={`${call.name}-${index}`} className="rounded-2xl border border-border bg-card px-3 py-2">
                  <p className="font-mono text-xs text-foreground">{call.server}/{call.name}</p>
                  <p className="mt-1 text-xs text-muted-foreground">{call.status ?? 'observed'}</p>
                </div>
              )) : (
                <p className="text-sm text-muted-foreground">도구 호출이 감지되면 여기에 표시됩니다.</p>
              )}
            </div>
          </div>

          <div className="cm-card">
            <p className="cm-kicker">Run metadata</p>
            <dl className="mt-3 grid gap-2 text-sm">
              <div className="flex justify-between gap-3">
                <dt className="text-muted-foreground">Exit code</dt>
                <dd className="font-mono text-foreground">{result?.exit_code ?? '-'}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-muted-foreground">Duration</dt>
                <dd className="font-mono text-foreground">{formatDuration(result?.duration_ms)}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-muted-foreground">Stdout</dt>
                <dd className="font-mono text-foreground">{result?.stdout_truncated ? 'truncated' : result ? 'full' : '-'}</dd>
              </div>
            </dl>
          </div>
        </aside>
      </div>

      {result ? (
        <details className="cm-card">
          <summary className="cursor-pointer text-sm font-semibold text-foreground">Raw stdout / stderr 보기</summary>
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-2xl border border-border bg-muted/40 p-4 text-xs">{result.stdout || '<empty stdout>'}</pre>
            <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-2xl border border-border bg-muted/40 p-4 text-xs">{result.stderr || '<empty stderr>'}</pre>
          </div>
        </details>
      ) : null}
    </section>
  );
}
