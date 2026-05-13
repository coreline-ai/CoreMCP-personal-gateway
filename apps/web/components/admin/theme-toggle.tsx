'use client';

import { useEffect, useState } from 'react';

const THEME_STORAGE_KEY = 'coremcp_theme';

type Theme = 'dark' | 'light' | 'system';

function isTheme(value: string | null): value is Theme {
  return value === 'dark' || value === 'light' || value === 'system';
}

function resolveTheme(theme: Theme) {
  if (theme !== 'system') return theme;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function applyTheme(theme: Theme) {
  const resolved = resolveTheme(theme);
  document.documentElement.classList.toggle('dark', resolved === 'dark');
  document.documentElement.style.colorScheme = resolved;
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>('dark');

  useEffect(() => {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    const nextTheme = isTheme(stored) ? stored : 'dark';
    setTheme(nextTheme);
    applyTheme(nextTheme);

    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const handleSystemChange = () => {
      if ((window.localStorage.getItem(THEME_STORAGE_KEY) ?? 'dark') === 'system') {
        applyTheme('system');
      }
    };

    media.addEventListener('change', handleSystemChange);
    return () => media.removeEventListener('change', handleSystemChange);
  }, []);

  function handleThemeChange(value: Theme) {
    setTheme(value);
    window.localStorage.setItem(THEME_STORAGE_KEY, value);
    applyTheme(value);
  }

  return (
    <label className="mt-3 grid gap-1 text-xs font-medium text-muted-foreground">
      Theme
      <select
        aria-label="Theme"
        value={theme}
        onChange={(event) => handleThemeChange(event.target.value as Theme)}
        className="cm-select py-1.5 text-xs"
      >
        <option value="dark">Dark 기본</option>
        <option value="light">Light</option>
        <option value="system">System</option>
      </select>
    </label>
  );
}
