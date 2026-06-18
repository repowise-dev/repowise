"use client";

import { useState } from "react";
import { ArrowUpRight, ChevronDown, ChevronRight, Flame } from "lucide-react";
import { biomarkerLabel } from "./biomarker-glossary";
import type { BiomarkerDetailsRecord } from "./biomarker-details";
import { SEVERITY_CHIP, SEVERITY_LABEL, type Severity } from "./tokens";

export interface HotFunctionFinding {
  id: string;
  file_path: string;
  biomarker_type: string;
  severity: Severity;
  function_name: string | null;
  line_start?: number | null;
  health_impact: number;
  reason: string;
  details?: BiomarkerDetailsRecord | null;
  /** Matching symbol id (server join); enables the symbol-page link. */
  symbol_id?: string | null;
}

export interface HotFunctionsPanelProps {
  findings: HotFunctionFinding[];
  /** Maximum function entries to render. */
  limit?: number;
  /** Click → open file drawer (and ideally scroll to the function). */
  onSelect?: ((f: HotFunctionFinding) => void) | undefined;
  /** Href for the function name → canonical symbol page. */
  symbolHrefFor?: ((f: HotFunctionFinding) => string | undefined) | undefined;
  /** Start collapsed, showing only `collapsedCount` rows behind a toggle. */
  collapsible?: boolean;
  /** Rows shown while collapsed. */
  collapsedCount?: number;
}

const HOT_FUNCTION_BIOMARKERS = new Set([
  "function_hotspot",
  "code_age_volatility",
  "complex_conditional",
]);

interface AggregatedFunction {
  key: string;
  file_path: string;
  function_name: string;
  total_impact: number;
  worst_severity: Severity;
  biomarkers: Set<string>;
  representative: HotFunctionFinding;
}

const SEVERITY_RANK: Record<Severity, number> = {
  low: 0,
  medium: 1,
  high: 2,
  critical: 3,
};

function aggregate(findings: HotFunctionFinding[]): AggregatedFunction[] {
  const byKey = new Map<string, AggregatedFunction>();
  for (const f of findings) {
    if (!HOT_FUNCTION_BIOMARKERS.has(f.biomarker_type)) continue;
    if (!f.function_name) continue;
    const key = `${f.file_path}::${f.function_name}`;
    const existing = byKey.get(key);
    if (existing) {
      existing.total_impact += f.health_impact;
      existing.biomarkers.add(f.biomarker_type);
      if (SEVERITY_RANK[f.severity] > SEVERITY_RANK[existing.worst_severity]) {
        existing.worst_severity = f.severity;
      }
      if (f.health_impact > existing.representative.health_impact) {
        existing.representative = f;
      }
    } else {
      byKey.set(key, {
        key,
        file_path: f.file_path,
        function_name: f.function_name,
        total_impact: f.health_impact,
        worst_severity: f.severity,
        biomarkers: new Set([f.biomarker_type]),
        representative: f,
      });
    }
  }
  return [...byKey.values()].sort((a, b) => b.total_impact - a.total_impact);
}

export function HotFunctionsPanel({
  findings,
  limit = 15,
  onSelect,
  symbolHrefFor,
  collapsible = false,
  collapsedCount = 4,
}: HotFunctionsPanelProps) {
  const all = aggregate(findings).slice(0, limit);
  const [open, setOpen] = useState(!collapsible);
  if (all.length === 0) return null;
  const rows = open ? all : all.slice(0, collapsedCount);
  const headerInner = (
    <>
      {collapsible ? (
        open ? (
          <ChevronDown className="h-4 w-4 text-[var(--color-text-tertiary)]" aria-hidden="true" />
        ) : (
          <ChevronRight className="h-4 w-4 text-[var(--color-text-tertiary)]" aria-hidden="true" />
        )
      ) : null}
      <Flame className="h-4 w-4 text-[var(--color-warning)]" aria-hidden="true" />
      <h2 className="text-sm font-medium text-[var(--color-text-primary)]">Hot functions</h2>
      <span className="text-xs text-[var(--color-text-tertiary)]">
        Top functions where churn, age, and complexity collide
      </span>
      <span className="ml-auto text-xs text-[var(--color-text-tertiary)]">
        {all.length} function{all.length === 1 ? "" : "s"}
      </span>
    </>
  );

  return (
    <section className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)]">
      {collapsible ? (
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="flex w-full items-center gap-2 px-4 py-3 border-b border-[var(--color-border-default)] text-left hover:bg-[var(--color-bg-elevated)] transition-colors"
        >
          {headerInner}
        </button>
      ) : (
        <header className="flex items-center gap-2 px-4 py-3 border-b border-[var(--color-border-default)]">
          {headerInner}
        </header>
      )}
      <ul className="divide-y divide-[var(--color-border-default)]">
        {rows.map((row) => {
          const interactive = !!onSelect;
          return (
            <li
              key={row.key}
              className={`p-3 ${interactive ? "cursor-pointer hover:bg-[var(--color-bg-elevated)]" : ""}`}
              onClick={interactive ? () => onSelect!(row.representative) : undefined}
            >
              <div className="flex items-center gap-2 flex-wrap">
                <span
                  className={`inline-block rounded px-1.5 py-px text-[10px] uppercase font-semibold ${SEVERITY_CHIP[row.worst_severity]}`}
                >
                  {SEVERITY_LABEL[row.worst_severity]}
                </span>
                {(() => {
                  const href = symbolHrefFor
                    ? symbolHrefFor(row.representative)
                    : undefined;
                  return href ? (
                    <a
                      href={href}
                      onClick={(e) => e.stopPropagation()}
                      className="text-xs font-mono text-[var(--color-accent-primary)] hover:underline"
                      title="Open symbol page"
                    >
                      {row.function_name}
                    </a>
                  ) : (
                    <span className="text-xs font-mono text-[var(--color-text-primary)]">
                      {row.function_name}
                    </span>
                  );
                })()}
                <span className="text-xs font-mono text-[var(--color-text-tertiary)] truncate">
                  {row.file_path}
                </span>
                <span className="ml-auto inline-flex items-center gap-2">
                  <span className="text-xs tabular-nums text-[var(--color-error)]">
                    −{row.total_impact.toFixed(2)}
                  </span>
                  {interactive ? (
                    <ArrowUpRight className="h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
                  ) : null}
                </span>
              </div>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {[...row.biomarkers].map((b) => (
                  <span
                    key={b}
                    className="inline-block rounded border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-1.5 py-px text-[10px] text-[var(--color-text-secondary)]"
                  >
                    {biomarkerLabel(b)}
                  </span>
                ))}
              </div>
            </li>
          );
        })}
      </ul>
      {collapsible && all.length > collapsedCount ? (
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="w-full border-t border-[var(--color-border-default)] px-4 py-2 text-xs font-medium text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] transition-colors"
        >
          {open ? "Show less" : `Show all ${all.length}`}
        </button>
      ) : null}
    </section>
  );
}
