import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'CoreMCP Web Admin',
  description: '개인 MCP 도구함을 관리하는 CoreMCP Web Admin UI'
};

export const dynamic = 'force-dynamic';

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko" className="dark" suppressHydrationWarning>
      <body>{children}</body>
    </html>
  );
}
