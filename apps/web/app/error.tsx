'use client';

import Link from 'next/link';

interface ErrorPageProps {
  error: Error & { digest?: string };
  reset: () => void;
}

export default function ErrorPage({ error, reset }: ErrorPageProps) {
  return (
    <main className="cm-app-shell">
      <section className="cm-page-frame">
        <div className="cm-page-content">
          <div className="mx-auto grid max-w-xl gap-4 rounded-xl border border-border bg-card p-6">
            <h1 className="text-base font-medium text-foreground">페이지를 표시할 수 없습니다</h1>
            <p className="text-sm text-muted-foreground">
              요청 처리 중 예기치 못한 오류가 발생했습니다. 같은 페이지를 다시 시도하거나
              사이드바에서 다른 메뉴로 이동해 주세요.
            </p>
            {error.digest ? (
              <p className="font-mono text-xs text-muted-foreground">
                ref: <span className="select-all">{error.digest}</span>
              </p>
            ) : null}
            <div className="flex gap-2">
              <button type="button" onClick={reset} className="cm-button cm-button-primary cm-button-sm">
                다시 시도
              </button>
              <Link href="/" className="cm-button cm-button-secondary cm-button-sm">
                홈으로
              </Link>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
