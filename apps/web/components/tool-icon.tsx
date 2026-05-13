'use client';

import { useEffect, useState } from 'react';
import type { CoreMcpToolSummary } from '@coremcp/shared-types';

export const TOOL_ICON_FALLBACK_SRC = '/icon-fallback.png';

type ToolIconTool = Pick<CoreMcpToolSummary, 'name' | 'title' | 'icons'>;

export interface ToolIconProps {
  tool?: ToolIconTool;
  src?: string | null;
  alt?: string;
  size?: number;
  className?: string;
}

export function getToolIconSrc(tool?: Pick<CoreMcpToolSummary, 'icons'>, explicitSrc?: string | null): string {
  const iconSrc = tool?.icons?.[0]?.src;
  return explicitSrc ?? iconSrc ?? TOOL_ICON_FALLBACK_SRC;
}

export function ToolIcon({ tool, src, alt, size = 32, className = '' }: ToolIconProps) {
  const preferredSrc = getToolIconSrc(tool, src);
  const [currentSrc, setCurrentSrc] = useState(preferredSrc);
  const label = alt ?? tool?.title ?? tool?.name ?? 'MCP tool icon';

  useEffect(() => {
    setCurrentSrc(preferredSrc);
  }, [preferredSrc]);

  return (
    <img
      src={currentSrc}
      alt={label}
      width={size}
      height={size}
      loading="lazy"
      decoding="async"
      className={`rounded-lg border border-border bg-card object-contain p-1 ${className}`}
      onError={(event) => {
        if (event.currentTarget.src.endsWith(TOOL_ICON_FALLBACK_SRC)) return;
        setCurrentSrc(TOOL_ICON_FALLBACK_SRC);
      }}
    />
  );
}
