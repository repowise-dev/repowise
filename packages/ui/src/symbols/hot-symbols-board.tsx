"use client";

import { useState } from "react";
import { ArrowRight, ChevronDown, ChevronRight, Flame } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { Badge } from "../ui/badge";
import { cn } from "../lib/cn";
import type { CodeSymbol } from "@repowise-dev/types/symbols";

export interface HotSymbol {
  symbol: CodeSymbol;
  /** Composite hotness score (higher = hotter). */
  score: number;
  pagerank?: number;
}

export interface HotSymbolsBoardProps {
  items: HotSymbol[];
  /** Cap items rendered (default 8). */
  limit?: number;
  onSelect?: (sym: CodeSymbol) => void;
  className?: string;
  /**
   * When true (default) the board starts collapsed — header only, content
   * hidden behind an expand toggle. The table below it is the primary
   * surface; this board is a secondary preview, so keeping it folded by
   * default avoids stealing top-of-page real estate.
   */
  defaultCollapsed?: boolean;
}

export function HotSymbolsBoard({
  items,
  limit = 8,
  onSelect,
  className,
  defaultCollapsed = true,
}: HotSymbolsBoardProps) {
  const [open, setOpen] = useState(!defaultCollapsed);
  const top = items.slice(0, limit);
  if (top.length === 0) {
    return null;
  }
  const max = top[0]?.score ?? 1;

  return (
    <Card className={className}>
      <CardHeader className="pb-2">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="w-full text-left -my-1 -mx-1 rounded px-1 py-1 hover:bg-[var(--color-bg-elevated)] transition-colors"
        >
          <CardTitle className="text-sm flex items-center gap-2">
            {open ? (
              <ChevronDown className="h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
            )}
            <Flame className="h-4 w-4 text-orange-500" />
            Hot symbols
            <span className="text-[10px] font-normal text-[var(--color-text-tertiary)] uppercase tracking-wider">
              churn × centrality × complexity
            </span>
            <span className="ml-auto text-[10px] font-normal text-[var(--color-text-tertiary)] tabular-nums">
              {top.length}
            </span>
          </CardTitle>
        </button>
      </CardHeader>
      {open && (
      <CardContent className="pt-0">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          {top.map(({ symbol, score }) => {
            const intensity = max > 0 ? score / max : 0;
            return (
              <button
                key={symbol.id}
                onClick={onSelect ? () => onSelect(symbol) : undefined}
                disabled={!onSelect}
                className={cn(
                  "group rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-3 py-2 text-left transition-colors",
                  onSelect && "hover:border-[var(--color-accent-primary)] cursor-pointer",
                )}
                title={`${symbol.file_path}:${symbol.start_line}`}
              >
                <div className="flex items-center justify-between gap-2 mb-1">
                  <span className="font-mono text-xs text-[var(--color-text-primary)] truncate">
                    {symbol.name}
                  </span>
                  <ArrowRight className="h-3 w-3 text-[var(--color-text-tertiary)] shrink-0 opacity-0 group-hover:opacity-100 transition-opacity" />
                </div>
                <div className="flex flex-wrap items-center gap-1 mb-1.5">
                  <Badge variant="outline" className="text-[10px] h-4">
                    {symbol.kind}
                  </Badge>
                  {symbol.complexity_estimate > 10 && (
                    <Badge variant="stale" className="text-[10px] h-4">
                      cx {symbol.complexity_estimate}
                    </Badge>
                  )}
                </div>
                <p className="text-[10px] font-mono text-[var(--color-text-tertiary)] truncate">
                  {symbol.file_path}:{symbol.start_line}
                </p>
                <div className="mt-1.5 h-1 rounded-full bg-[var(--color-bg-inset)] overflow-hidden">
                  <div
                    className="h-full rounded-full bg-orange-500"
                    style={{ width: `${Math.round(intensity * 100)}%` }}
                  />
                </div>
              </button>
            );
          })}
        </div>
      </CardContent>
      )}
    </Card>
  );
}
