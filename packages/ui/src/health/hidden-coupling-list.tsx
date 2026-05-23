"use client";

import { ArrowLeftRight } from "lucide-react";
import type { BiomarkerDetailsRecord } from "./biomarker-details";
import { SEVERITY_CHIP, SEVERITY_LABEL, type Severity } from "./tokens";

export interface HiddenCouplingFinding {
  id: string;
  file_path: string;
  biomarker_type: string;
  severity: Severity;
  health_impact: number;
  details?: BiomarkerDetailsRecord | null;
}

export interface HiddenCouplingListProps {
  findings: HiddenCouplingFinding[];
  limit?: number;
  onSelect?: ((path: string) => void) | undefined;
  hrefFor?: ((path: string) => string) | undefined;
}

interface CouplingPair {
  key: string;
  a: string;
  b: string;
  correlation: number;
  co_change_count: number;
  worst_severity: Severity;
  impact: number;
}

const SEVERITY_RANK: Record<Severity, number> = {
  low: 0,
  medium: 1,
  high: 2,
  critical: 3,
};

function num(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v !== "" && Number.isFinite(Number(v))) {
    return Number(v);
  }
  return null;
}

function str(v: unknown): string | null {
  return typeof v === "string" && v.length > 0 ? v : null;
}

function aggregate(findings: HiddenCouplingFinding[]): CouplingPair[] {
  const byKey = new Map<string, CouplingPair>();
  for (const f of findings) {
    if (f.biomarker_type !== "hidden_coupling") continue;
    const partner = str(f.details?.partner);
    if (!partner) continue;
    const sorted = [f.file_path, partner].sort();
    const key = sorted.join("|");
    const corr = num(f.details?.correlation) ?? 0;
    const co = num(f.details?.co_change_count) ?? 0;
    const existing = byKey.get(key);
    if (existing) {
      existing.correlation = Math.max(existing.correlation, corr);
      existing.co_change_count = Math.max(existing.co_change_count, co);
      existing.impact = Math.max(existing.impact, f.health_impact);
      if (SEVERITY_RANK[f.severity] > SEVERITY_RANK[existing.worst_severity]) {
        existing.worst_severity = f.severity;
      }
    } else {
      byKey.set(key, {
        key,
        a: sorted[0]!,
        b: sorted[1]!,
        correlation: corr,
        co_change_count: co,
        worst_severity: f.severity,
        impact: f.health_impact,
      });
    }
  }
  return [...byKey.values()].sort(
    (a, b) => b.correlation - a.correlation || b.impact - a.impact,
  );
}

export function HiddenCouplingList({
  findings,
  limit = 15,
  onSelect,
  hrefFor,
}: HiddenCouplingListProps) {
  const rows = aggregate(findings).slice(0, limit);
  if (rows.length === 0) return null;

  return (
    <section className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)]">
      <header className="flex items-center gap-2 px-4 py-3 border-b border-[var(--color-border-default)]">
        <ArrowLeftRight className="h-4 w-4 text-violet-500" aria-hidden="true" />
        <h2 className="text-sm font-medium text-[var(--color-text-primary)]">
          Hidden coupling pairs
        </h2>
        <span className="text-xs text-[var(--color-text-tertiary)]">
          Files that co-change without an import edge
        </span>
        <span className="ml-auto text-xs text-[var(--color-text-tertiary)]">
          {rows.length} pair{rows.length === 1 ? "" : "s"}
        </span>
      </header>
      <ul className="divide-y divide-[var(--color-border-default)]">
        {rows.map((row) => (
          <li key={row.key} className="p-3 space-y-1.5">
            <div className="flex items-center gap-2 flex-wrap">
              <span
                className={`inline-block rounded px-1.5 py-px text-[10px] uppercase font-semibold ${SEVERITY_CHIP[row.worst_severity]}`}
              >
                {SEVERITY_LABEL[row.worst_severity]}
              </span>
              <span className="ml-auto inline-flex items-center gap-3 text-xs tabular-nums text-[var(--color-text-tertiary)]">
                <span title="Correlation = co-change count / min(commits A, commits B)">
                  {Math.round(row.correlation * 100)}%
                </span>
                <span>· {row.co_change_count} co-commits</span>
              </span>
            </div>
            <div className="grid grid-cols-1 gap-1 text-[11px] font-mono">
              <PairLink path={row.a} onSelect={onSelect} hrefFor={hrefFor} />
              <span className="text-[var(--color-text-tertiary)] inline-flex items-center gap-1">
                <ArrowLeftRight className="h-3 w-3" aria-hidden="true" />
              </span>
              <PairLink path={row.b} onSelect={onSelect} hrefFor={hrefFor} />
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function PairLink({
  path,
  onSelect,
  hrefFor,
}: {
  path: string;
  onSelect?: ((path: string) => void) | undefined;
  hrefFor?: ((path: string) => string) | undefined;
}) {
  const href = hrefFor?.(path);
  if (onSelect) {
    return (
      <button
        type="button"
        onClick={() => onSelect(path)}
        className="text-left text-[var(--color-accent-primary)] hover:underline truncate"
      >
        {path}
      </button>
    );
  }
  if (href) {
    return (
      <a
        href={href}
        className="text-[var(--color-accent-primary)] hover:underline truncate"
      >
        {path}
      </a>
    );
  }
  return <span className="text-[var(--color-text-primary)] truncate">{path}</span>;
}
