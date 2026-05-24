import type { Metadata } from 'next';
import { headers } from 'next/headers';
import './globals.css';

export const metadata: Metadata = {
  title: 'CoreMCP Web Admin',
  description: '개인 MCP 도구함을 관리하는 CoreMCP Web Admin UI'
};

export const dynamic = 'force-dynamic';

const themeBootScript = `(function(){try{var s=localStorage.getItem('coremcp_theme')||'dark';var t=s==='system'?(window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):s;var c=document.documentElement.classList;if(t==='dark'){c.add('dark');}else{c.remove('dark');}document.documentElement.style.colorScheme=t;}catch(e){document.documentElement.classList.add('dark');document.documentElement.style.colorScheme='dark';}})();`;

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const nonce = (await headers()).get('x-nonce') ?? undefined;

  return (
    <html lang="ko" suppressHydrationWarning>
      <head>
        <script nonce={nonce} dangerouslySetInnerHTML={{ __html: themeBootScript }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
