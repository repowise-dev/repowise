"use client";

import { memo, useMemo } from "react";
import { ArrowRight } from "lucide-react";
import type {
  SymbolBodyCall,
  SymbolRelationGroup,
} from "@repowise-dev/types/symbols";
import { truncatePath } from "../lib/format";
import { originDescriptor } from "../graph/edge-provenance";
import { relationHint, relationLabel } from "../graph/symbol-relations";

interface SymbolCallGraphProps {
  centerName: string;
  /** `calls` edges only. Heritage and wiring arrive in `relations`. */
  callers: SymbolBodyCall[];
  callees: SymbolBodyCall[];
  /** True edge counts. `callers.length` is the server's row cap, so falling
   *  back to it is what made "Called by (40)" appear over 1,524 edges. */
  callerTotal?: number;
  calleeTotal?: number;
  relations?: SymbolRelationGroup<SymbolBodyCall>[];
  symbolHref?: (id: string) => string;
  /** Cap per column so the mini-graph stays compact. */
  limit?: number;
}

/**
 * Memoised because the drawer's wrapper streams five independent SWR feeds
 * into this subtree, so every feed that resolves re-renders every row —
 * rule 16's shape with a fetch waterfall in place of a token stream.
 *
 * This only bites if the caller keeps the row objects referentially stable.
 * `symbol-drawer-wrapper.tsx` memoises them on their own feed for exactly
 * that reason; building them inside a memo that also depends on git or
 * co-change data re-mints every row and the boundary here does nothing.
 */
const CallNode = memo(function CallNode({
  entry,
  symbolHref,
}: {
  entry: SymbolBodyCall;
  symbolHref?: (id: string) => string;
}) {
  // The origin replaces a green/amber/grey confidence dot that carried no key
  // and reached for the health ramp to say something that is not a health
  // band. Only a name match is marked; everything else is ordinary resolution.
  const origin = originDescriptor(entry.resolution_origin);
  const inner = (
    <div
      className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2 py-1.5 transition-colors hover:border-[var(--color-border-hover)]"
      {...(origin ? { title: `Resolved because ${origin.because}` } : {})}
    >
      <div className="truncate font-mono text-xs text-[var(--color-text-primary)]">
        {entry.name}
      </div>
      <div className="flex items-baseline gap-1.5 text-[10px] text-[var(--color-text-tertiary)]">
        <span className="min-w-0 flex-1 truncate" title={entry.file}>
          {truncatePath(entry.file, 28)}
        </span>
        {/* Not mono: this is an authored category, not a value the machine
            emitted, so it takes the ordinary face like `SeverityMark`. */}
        {origin?.tier === "name_match" && <span className="shrink-0">name match</span>}
      </div>
    </div>
  );
  if (!symbolHref) return inner;
  return (
    <a href={symbolHref(entry.symbol_id)} className="block no-underline">
      {inner}
    </a>
  );
});

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
      {children}
    </p>
  );
}

/**
 * A labelled list of edges with the count it was cut from. The count is the
 * server's unbounded total, so the footer states a real remainder instead of
 * subtracting one cap from another.
 */
function RelationList({
  label,
  total,
  rows,
  limit,
  hint,
  symbolHref,
}: {
  label: string;
  total: number;
  rows: SymbolBodyCall[];
  limit: number;
  hint?: string | undefined;
  symbolHref?: ((id: string) => string) | undefined;
}) {
  const shown = rows.slice(0, limit);
  const remainder = total - shown.length;
  return (
    // `min-w-0`: a grid track sizes to its content, so a long single-token
    // symbol name defeats the row's `truncate` and widens the page.
    <div className="min-w-0 space-y-1.5">
      <SectionLabel>
        {label} <span className="tabular-nums">({total.toLocaleString()})</span>
      </SectionLabel>
      {hint && (
        <p className="text-[10px] leading-snug text-[var(--color-text-tertiary)]">{hint}</p>
      )}
      {shown.length === 0 ? (
        <p className="text-xs italic text-[var(--color-text-tertiary)]">None</p>
      ) : (
        shown.map((c) => (
          <CallNode
            key={`${c.symbol_id}-${c.edge_type}`}
            entry={c}
            {...(symbolHref ? { symbolHref } : {})}
          />
        ))
      )}
      {remainder > 0 && (
        <p className="text-[10px] tabular-nums text-[var(--color-text-tertiary)]">
          +{remainder.toLocaleString()} more
        </p>
      )}
    </div>
  );
}

/**
 * A compact centered call-graph: callers feed into the centered symbol, which
 * feeds the callees. Empty columns read as "no edges".
 *
 * Only `calls` edges reach those two columns. Every other relation the engine
 * resolves — heritage, framework wiring, bare references — is listed beneath
 * under its own verb, because serving them as callers was both wrong and
 * lossy: one confidence-ranked cap across all seven kinds meant `TestCase`,
 * with 3 callers and 868 subclasses, showed 40 subclasses and none of its
 * callers.
 */
export function SymbolCallGraph({
  centerName,
  callers,
  callees,
  callerTotal,
  calleeTotal,
  relations,
  symbolHref,
  limit = 6,
}: SymbolCallGraphProps) {
  // One hint per group, on its first section, so a run of heritage rows is
  // explained once rather than per verb — rule 10 on markers every row carries.
  const sections = useMemo(() => {
    const seen = new Set<string>();
    return (relations ?? []).map((rel) => {
      const first = !seen.has(rel.group);
      seen.add(rel.group);
      return { rel, hint: first ? relationHint(rel.group) : undefined };
    });
  }, [relations]);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-3">
        <RelationList
          label="Called by"
          total={callerTotal ?? callers.length}
          rows={callers}
          limit={limit}
          symbolHref={symbolHref}
        />

        <div className="flex flex-col items-center gap-1">
          <ArrowRight className="h-4 w-4 text-[var(--color-text-tertiary)]" />
          <div className="max-w-[140px] truncate rounded-md border border-[var(--color-accent-primary)] bg-[var(--color-accent-primary)]/10 px-3 py-2 text-center font-mono text-xs font-semibold text-[var(--color-text-primary)]">
            {centerName}
          </div>
          <ArrowRight className="h-4 w-4 text-[var(--color-text-tertiary)]" />
        </div>

        <RelationList
          label="Calls"
          total={calleeTotal ?? callees.length}
          rows={callees}
          limit={limit}
          symbolHref={symbolHref}
        />
      </div>

      {sections.length > 0 && (
        <div className="grid gap-4 border-t border-[var(--color-border-default)] pt-3 sm:grid-cols-2">
          {sections.map(({ rel, hint }) => (
            <RelationList
              key={`${rel.direction}-${rel.edge_type}`}
              label={relationLabel(rel.edge_type, rel.direction)}
              total={rel.total}
              rows={rel.rows}
              limit={limit}
              hint={hint}
              symbolHref={symbolHref}
            />
          ))}
        </div>
      )}
    </div>
  );
}
