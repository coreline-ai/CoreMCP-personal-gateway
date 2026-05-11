export type McpProtocolVersion = '2025-11-25' | '2025-06-18';

export interface CoreMcpToolIcon {
  src: string;
  mimeType?: string;
  sizes?: string[];
}

export interface CoreMcpToolSummary {
  name: string;
  title?: string;
  description?: string;
  icons?: CoreMcpToolIcon[];
}
