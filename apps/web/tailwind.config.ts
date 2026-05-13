import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}', './lib/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        background: 'var(--background)',
        foreground: 'var(--foreground)',
        card: 'var(--card)',
        'card-foreground': 'var(--card-foreground)',
        muted: {
          DEFAULT: 'var(--muted)',
          foreground: 'var(--muted-foreground)'
        },
        secondary: {
          DEFAULT: 'var(--secondary)',
          foreground: 'var(--secondary-foreground)'
        },
        border: 'var(--border)',
        input: 'var(--input)',
        ring: 'var(--ring)',
        destructive: 'var(--destructive)',
        success: 'var(--success)',
        warning: 'var(--warning)',
        info: 'var(--info)',
        ink: 'var(--foreground)',
        panel: 'var(--card)',
        line: 'var(--border)',
        brand: {
          DEFAULT: 'var(--brand)',
          foreground: 'var(--brand-foreground)',
          50: 'color-mix(in oklab, var(--brand) 8%, var(--background))',
          100: 'color-mix(in oklab, var(--brand) 14%, var(--background))',
          500: 'var(--brand)',
          600: 'color-mix(in oklab, var(--brand) 84%, var(--foreground))',
          700: 'color-mix(in oklab, var(--brand) 72%, var(--foreground))',
          800: 'color-mix(in oklab, var(--brand) 64%, var(--foreground))'
        }
      },
      boxShadow: {
        soft: 'none'
      },
      fontFamily: {
        sans: ['Pretendard', 'Inter', 'ui-sans-serif', 'system-ui', 'Apple SD Gothic Neo', 'sans-serif'],
        mono: ['SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'monospace']
      }
    }
  },
  plugins: []
};

export default config;
