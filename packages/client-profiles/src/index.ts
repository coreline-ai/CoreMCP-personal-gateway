export type ClientProfileId = 'claude_code' | 'openclaw' | 'chatgpt' | 'cursor' | 'windsurf' | 'other';

export interface ClientProfile {
  id: ClientProfileId;
  displayName: string;
  preferredAuth: 'admin_bearer' | 'client_bearer' | 'oauth';
  priority: 'P0' | 'P1' | 'P2' | 'P3';
}

export const clientProfiles: ClientProfile[] = [
  { id: 'claude_code', displayName: 'Claude Code', preferredAuth: 'client_bearer', priority: 'P0' },
  { id: 'openclaw', displayName: 'OpenClaw', preferredAuth: 'client_bearer', priority: 'P1' },
  { id: 'chatgpt', displayName: 'ChatGPT Custom MCP', preferredAuth: 'oauth', priority: 'P2' },
  { id: 'cursor', displayName: 'Cursor', preferredAuth: 'client_bearer', priority: 'P2' },
  { id: 'windsurf', displayName: 'Windsurf', preferredAuth: 'client_bearer', priority: 'P2' }
];
