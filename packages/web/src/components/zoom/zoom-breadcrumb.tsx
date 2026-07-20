"use client";

/**
 * "You are here" breadcrumb for the zoom canvas. Renders the root -> focus chain
 * the canvas reports; clicking a crumb flies the camera to that node. Purely
 * presentational: the page owns the chain state and the fly callback.
 */

import { ChevronRight, Home } from "lucide-react";
import type { ZoomNode } from "@repowise-dev/ui/zoom";

interface ZoomBreadcrumbProps {
  chain: ZoomNode[];
  onCrumb: (id: string) => void;
}

export function ZoomBreadcrumb({ chain, onCrumb }: ZoomBreadcrumbProps) {
  if (chain.length === 0) return null;
  const root = chain[0]!;
  const rest = chain.slice(1);

  return (
    <nav
      aria-label="Zoom location"
      className="flex min-w-0 items-center gap-1 overflow-x-auto text-xs text-[var(--color-text-secondary)]"
    >
      <button
        type="button"
        onClick={() => onCrumb(root.id)}
        className="flex shrink-0 items-center gap-1 rounded px-1.5 py-1 hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
        title={root.name}
      >
        <Home className="h-3.5 w-3.5" />
        <span className="hidden sm:inline">{root.name}</span>
      </button>
      {rest.map((node, i) => {
        const isLast = i === rest.length - 1;
        return (
          <span key={node.id} className="flex shrink-0 items-center gap-1">
            <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
            {isLast ? (
              <span className="max-w-[16rem] truncate px-1 py-1 font-medium text-[var(--color-text-primary)]">
                {node.name}
              </span>
            ) : (
              <button
                type="button"
                onClick={() => onCrumb(node.id)}
                className="max-w-[12rem] truncate rounded px-1.5 py-1 hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
                title={node.path || node.name}
              >
                {node.name}
              </button>
            )}
          </span>
        );
      })}
    </nav>
  );
}
