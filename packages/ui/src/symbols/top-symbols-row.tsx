"use client";

import * as React from "react";
import { ChevronRight, AlertCircle } from "lucide-react";
import { Skeleton } from "../ui/skeleton";
import { Badge } from "../ui/badge";
import { cn } from "../lib/cn";
import type { CodeSymbol } from "@repowise-dev/types/symbols";

interface TopSymbolsRowProps {
  symbols: CodeSymbol[] | undefined;
  loading?: boolean | undefined;
  error?: string | undefined;
  /** Optional click handler — typically opens the SymbolDrawer in the host. */
  onSelect?: ((symbol: CodeSymbol) => void) | undefined;
  /** Link to the full /symbols page filtered to this file, when host provides one. */
  seeAllHref?: string | undefined;
  className?: string | undefined;
}

const KIND_LABEL: Record<string, string> = {
  function: "fn",
  method: "m",
  class: "class",
  interface: "iface",
  struct: "struct",
  enum: "enum",
  trait: "trait",
  variable: "var",
  type: "type",
  module: "mod",
};

/**
 * Inline panel shown when a hotspot row is expanded. Renders the
 * importance-ranked top symbols for the underlying file. Presentational —
 * the host owns fetching (typically a SWR call against `/api/symbols` with
 * the `file_path` filter).
 */
export function TopSymbolsRow({
  symbols,
  loading,
  error,
  onSelect,
  seeAllHref,
  className,
}: TopSymbolsRowProps) {
  if (loading && (!symbols || symbols.length === 0)) {
    return (
      <div className={cn("space-y-2", className)}>
        <div className="text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
          Top symbols
        </div>
        <div className="grid gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-7 w-full" />
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div
        className={cn(
          "flex items-center gap-2 text-xs text-[var(--color-text-secondary)]",
          className,
        )}
      >
        <AlertCircle className="h-3.5 w-3.5 text-red-400" />
        Couldn&apos;t load symbols: {error}
      </div>
    );
  }

  if (!symbols || symbols.length === 0) {
    return (
      <div className={cn("text-xs text-[var(--color-text-tertiary)]", className)}>
        No indexed symbols in this file yet.
      </div>
    );
  }

  return (
    <div className={cn("space-y-2", className)}>
      <div className="flex items-center justify-between">
        <div className="text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
          Top symbols ({symbols.length})
        </div>
        {seeAllHref && (
          <a
            href={seeAllHref}
            className="inline-flex items-center gap-0.5 text-[11px] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
          >
            See all
            <ChevronRight className="h-3 w-3" />
          </a>
        )}
      </div>
      <div className="grid gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
        {symbols.map((s) => {
          const score = typeof s.importance_score === "number" ? s.importance_score : null;
          const isPublic = s.visibility === "public";
          return (
            <button
              key={s.id}
              type="button"
              onClick={onSelect ? () => onSelect(s) : undefined}
              className={cn(
                "group flex items-center gap-2 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2 py-1.5 text-left transition-colors",
                onSelect && "hover:border-[var(--color-accent-primary)] hover:bg-[var(--color-bg-hover)] cursor-pointer",
              )}
              title={s.qualified_name || s.name}
              aria-label={`Open symbol ${s.name}`}
            >
              <Badge variant="outline" className="shrink-0 px-1.5 py-0 text-[10px] uppercase">
                {KIND_LABEL[s.kind] ?? s.kind}
              </Badge>
              <span className="flex-1 truncate font-mono text-xs text-[var(--color-text-primary)]">
                {s.name}
              </span>
              {!isPublic && s.visibility && (
                <span className="shrink-0 text-[10px] text-[var(--color-text-tertiary)] uppercase">
                  {s.visibility}
                </span>
              )}
              {score !== null && (
                <span
                  className="shrink-0 tabular-nums text-[10px] text-[var(--color-text-tertiary)]"
                  title={`Importance score: ${score.toFixed(3)}`}
                >
                  {score.toFixed(2)}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
