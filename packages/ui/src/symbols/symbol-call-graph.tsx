"use client";

import { ArrowRight } from "lucide-react";
import type { SymbolBodyCall } from "@repowise-dev/types/symbols";
import { truncatePath } from "../lib/format";
import { cn } from "../lib/cn";

interface SymbolCallGraphProps {
  centerName: string;
  callers: SymbolBodyCall[];
  callees: SymbolBodyCall[];
  symbolHref?: (id: string) => string;
  /** Cap per column so the mini-graph stays compact. */
  limit?: number;
}

function CallNode({
  entry,
  symbolHref,
}: {
  entry: SymbolBodyCall;
  symbolHref?: (id: string) => string;
}) {
  const inner = (
    <div className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2 py-1.5 transition-colors hover:border-[var(--color-border-hover)]">
      <div className="flex items-center gap-1.5">
        <span
          className={cn(
            "h-1.5 w-1.5 shrink-0 rounded-full",
            (entry.confidence ?? 1) >= 0.9
              ? "bg-[var(--color-success)]"
              : (entry.confidence ?? 1) >= 0.7
                ? "bg-[var(--color-caution)]"
                : "bg-[var(--color-text-tertiary)]",
          )}
        />
        <span className="truncate font-mono text-xs text-[var(--color-text-primary)]">
          {entry.name}
        </span>
      </div>
      <div className="truncate pl-3 text-[10px] text-[var(--color-text-tertiary)]" title={entry.file}>
        {truncatePath(entry.file, 28)}
      </div>
    </div>
  );
  if (!symbolHref) return inner;
  return (
    <a href={symbolHref(entry.symbol_id)} className="block no-underline">
      {inner}
    </a>
  );
}

/**
 * A compact centered call-graph: callers feed into the centered symbol, which
 * feeds the callees. Replaces the two flat caller/callee lists with the graph
 * shape the data actually describes. Empty columns read as "no edges".
 */
export function SymbolCallGraph({
  centerName,
  callers,
  callees,
  symbolHref,
  limit = 6,
}: SymbolCallGraphProps) {
  const callersShown = callers.slice(0, limit);
  const calleesShown = callees.slice(0, limit);

  return (
    <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-3">
      <div className="space-y-1.5">
        <p className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
          Called by ({callers.length})
        </p>
        {callersShown.length === 0 ? (
          <p className="text-xs italic text-[var(--color-text-tertiary)]">None</p>
        ) : (
          callersShown.map((c) => (
            <CallNode key={`${c.symbol_id}-${c.edge_type}`} entry={c} {...(symbolHref ? { symbolHref } : {})} />
          ))
        )}
        {callers.length > limit && (
          <p className="text-[10px] text-[var(--color-text-tertiary)]">
            +{callers.length - limit} more
          </p>
        )}
      </div>

      <div className="flex flex-col items-center gap-1">
        <ArrowRight className="h-4 w-4 text-[var(--color-text-tertiary)]" />
        <div className="max-w-[140px] truncate rounded-md border border-[var(--color-accent-primary)] bg-[var(--color-accent-primary)]/10 px-3 py-2 text-center font-mono text-xs font-semibold text-[var(--color-text-primary)]">
          {centerName}
        </div>
        <ArrowRight className="h-4 w-4 text-[var(--color-text-tertiary)]" />
      </div>

      <div className="space-y-1.5">
        <p className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
          Calls ({callees.length})
        </p>
        {calleesShown.length === 0 ? (
          <p className="text-xs italic text-[var(--color-text-tertiary)]">None</p>
        ) : (
          calleesShown.map((c) => (
            <CallNode key={`${c.symbol_id}-${c.edge_type}`} entry={c} {...(symbolHref ? { symbolHref } : {})} />
          ))
        )}
        {callees.length > limit && (
          <p className="text-[10px] text-[var(--color-text-tertiary)]">
            +{callees.length - limit} more
          </p>
        )}
      </div>
    </div>
  );
}
