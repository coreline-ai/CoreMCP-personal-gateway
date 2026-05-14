import type { ComponentType, SVGProps } from 'react';

type Icon = ComponentType<SVGProps<SVGSVGElement>>;

const base = {
  width: 18,
  height: 18,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.6,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
};

// Brand: gateway with two downstream nodes — CoreMCP의 "여러 다운스트림 MCP 를 하나로 묶는 게이트웨이" 의미
export const BrandIcon: Icon = (props) => (
  <svg {...base} width={20} height={20} {...props}>
    <rect x="3.5" y="9" width="6" height="6" rx="1.4" />
    <circle cx="19" cy="6" r="2.2" />
    <circle cx="19" cy="18" r="2.2" />
    <path d="M9.5 11 L16.9 7" />
    <path d="M9.5 13 L16.9 17" />
  </svg>
);

// Dashboard: 2x2 grid
export const DashboardIcon: Icon = (props) => (
  <svg {...base} {...props}>
    <rect x="3.5" y="3.5" width="7" height="7" rx="1.2" />
    <rect x="13.5" y="3.5" width="7" height="7" rx="1.2" />
    <rect x="3.5" y="13.5" width="7" height="7" rx="1.2" />
    <rect x="13.5" y="13.5" width="7" height="7" rx="1.2" />
  </svg>
);

// Logs: lines on a card
export const LogsIcon: Icon = (props) => (
  <svg {...base} {...props}>
    <rect x="4" y="3.5" width="16" height="17" rx="1.6" />
    <path d="M7.5 8.5 H16.5" />
    <path d="M7.5 12 H16.5" />
    <path d="M7.5 15.5 H13.5" />
  </svg>
);

// Services: stacked server boxes
export const ServicesIcon: Icon = (props) => (
  <svg {...base} {...props}>
    <rect x="3.5" y="4" width="17" height="6" rx="1.2" />
    <rect x="3.5" y="14" width="17" height="6" rx="1.2" />
    <circle cx="7" cy="7" r="0.9" />
    <circle cx="7" cy="17" r="0.9" />
    <path d="M11 7 H17" />
    <path d="M11 17 H17" />
  </svg>
);

// Toolbox: chest with handle
export const ToolboxIcon: Icon = (props) => (
  <svg {...base} {...props}>
    <rect x="3.5" y="8" width="17" height="12" rx="1.6" />
    <path d="M9 8 V6.5 a1.5 1.5 0 0 1 1.5 -1.5 h3 a1.5 1.5 0 0 1 1.5 1.5 V8" />
    <path d="M3.5 13 H20.5" />
    <rect x="10.5" y="11.5" width="3" height="3" rx="0.6" />
  </svg>
);

// Playground: play triangle in rounded square
export const PlaygroundIcon: Icon = (props) => (
  <svg {...base} {...props}>
    <rect x="3.5" y="3.5" width="17" height="17" rx="2" />
    <path d="M10 8.5 L15.5 12 L10 15.5 Z" fill="currentColor" stroke="none" />
  </svg>
);

// Clients: user with plug/link — 외부 AI agent가 게이트웨이에 connect
export const ClientsIcon: Icon = (props) => (
  <svg {...base} {...props}>
    <circle cx="9" cy="8" r="3" />
    <path d="M3.5 19.5 c0.8 -3.5 3 -5 5.5 -5 s4.7 1.5 5.5 5" />
    <path d="M16 7 v3" />
    <path d="M20 7 v3" />
    <path d="M14.5 10 H21.5" />
    <path d="M18 10 V13.5" />
    <path d="M18 13.5 a2 2 0 0 1 -2 2 H14.5" />
  </svg>
);

// Settings: gear
export const SettingsIcon: Icon = (props) => (
  <svg {...base} {...props}>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 3.5 V6" />
    <path d="M12 18 V20.5" />
    <path d="M3.5 12 H6" />
    <path d="M18 12 H20.5" />
    <path d="M6 6 L7.8 7.8" />
    <path d="M16.2 16.2 L18 18" />
    <path d="M6 18 L7.8 16.2" />
    <path d="M16.2 7.8 L18 6" />
  </svg>
);

export const sectionIcons: Record<string, Icon> = {
  dashboard: DashboardIcon,
  logs: LogsIcon,
  services: ServicesIcon,
  toolbox: ToolboxIcon,
  playground: PlaygroundIcon,
  clients: ClientsIcon,
  settings: SettingsIcon,
};
