"use client";

import { useEffect } from "react";
import { ExternalLink, X } from "lucide-react";
import { biomarkerLabel, biomarkerInfo, CATEGORY_LABEL } from "./biomarker-glossary";
import { BiomarkerDetails, type BiomarkerDetailsRecord } from "./biomarker-details";
import { ScoreBreakdown, type ScoreBreakdownCategory } from "./score-breakdown";
import {
  SEVERITY_CHIP,
  SEVERITY_LABEL,
  scoreBadgeClass,
  type Severity,
} from "./tokens";

export interface HealthDrawerFinding {
  id: string;
  biomarker_type: string;
  severity: Severity;
  function_name: string | null;
  line_start: number | null;
  line_end: number | null;
  health_impact: number;
  reason: string;
  status?: string;
  details?: BiomarkerDetailsRecord | null;
}

export interface HealthDrawerMetric {
  file_path: string;
  score: number;
  max_ccn: number;
  max_nesting: number;
  nloc: number;
  module: string | null;
  duplication_pct?: number | null;
  line_coverage_pct?: number | null;
  has_test_file: boolean;
}

export interface HealthFileDrawerProps {
  open: boolean;
  onClose: () => void;
  loading?: boolean;
  metric?: HealthDrawerMetric | null;
  breakdown?: {
    score: number;
    total_deduction: number;
    categories: ScoreBreakdownCategory[];
  } | null;
  findings?: HealthDrawerFinding[];
  suggestions?: Record<string, string>;
  fileViewHref?: string;
  /** Build a per-line deep-link from the drawer's function:line span. */
  fileViewHrefFor?: ((lineStart: number) => string) | undefined;
  permalinkHref?: string;
  onPartnerSelect?: ((path: string) => void) | undefined;
  onPartnerHref?: ((path: string) => string) | undefined;
}

export function HealthFileDrawer({
  open,
  onClose,
  loading,
  metric,
  breakdown,
  findings = [],
  suggestions = {},
  fileViewHref,
  fileViewHrefFor,
  permalinkHref,
  onPartnerSelect,
  onPartnerHref,
}: HealthFileDrawerProps) {
  // ESC to close
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex" role="dialog" aria-modal="true">
      <button
        type="button"
        aria-label="Close drawer"
        onClick={onClose}
        className="flex-1 bg-black/50 backdrop-blur-[1px]"
      />
      <aside className="w-full max-w-[640px] bg-[var(--color-bg-surface)] border-l border-[var(--color-border-default)] overflow-y-auto shadow-xl">
        <header className="sticky top-0 z-10 flex items-start justify-between gap-3 border-b border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-4 py-3">
          <div className="min-w-0 flex-1">
            <p className="text-xs uppercase tracking-wider text-[var(--color-text-tertiary)]">File health</p>
            <p className="font-mono text-sm text-[var(--color-text-primary)] truncate" title={metric?.file_path}>
              {metric?.file_path ?? "Loading…"}
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {permalinkHref ? (
              <a
                href={permalinkHref}
                className="text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] inline-flex items-center gap-1"
                title="Open as a shareable page"
              >
                <ExternalLink className="h-3.5 w-3.5" />
                Permalink
              </a>
            ) : null}
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="rounded p-1 text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </header>

        <div className="px-4 py-4 space-y-5">
          {loading ? (
            <div className="text-sm text-[var(--color-text-tertiary)]">Loading…</div>
          ) : !metric ? (
            <div className="rounded border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-4 text-sm text-[var(--color-text-secondary)]">
              No metric for this file yet. Run <code>repowise init</code> or <code>repowise health</code>.
            </div>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <Stat label="Score" value={
                  <span className={`inline-flex items-baseline rounded px-2 py-0.5 font-bold tabular-nums ${scoreBadgeClass(metric.score)}`}>
                    {metric.score.toFixed(1)}
                    <span className="ml-0.5 text-[10px] font-normal opacity-70">/10</span>
                  </span>
                } />
                <Stat label="Max CCN" value={<span className="text-base font-semibold tabular-nums">{metric.max_ccn}</span>} />
                <Stat label="Nest" value={<span className="text-base font-semibold tabular-nums">{metric.max_nesting}</span>} />
                <Stat label="NLOC" value={<span className="text-base font-semibold tabular-nums">{metric.nloc}</span>} />
                <Stat label="Module" value={<span className="text-xs">{metric.module ?? "—"}</span>} />
                <Stat label="Tests" value={<span className="text-xs">{metric.has_test_file ? "Paired" : "None"}</span>} />
                <Stat label="Coverage" value={
                  <span className="text-xs tabular-nums">
                    {metric.line_coverage_pct == null ? "—" : `${metric.line_coverage_pct.toFixed(0)}%`}
                  </span>
                } />
                <Stat label="Duplication" value={
                  <span className="text-xs tabular-nums">
                    {metric.duplication_pct == null ? "—" : `${metric.duplication_pct.toFixed(0)}%`}
                  </span>
                } />
              </div>

              {fileViewHref ? (
                <a
                  href={fileViewHref}
                  className="inline-flex items-center gap-1.5 text-xs text-[var(--color-accent-primary)] hover:underline"
                >
                  <ExternalLink className="h-3 w-3" />
                  Open in file explorer
                </a>
              ) : null}

              {breakdown ? (
                <section className="space-y-2">
                  <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
                    Why this score?
                  </h3>
                  <ScoreBreakdown
                    score={breakdown.score}
                    totalDeduction={breakdown.total_deduction}
                    categories={breakdown.categories}
                  />
                </section>
              ) : null}

              {findings.length > 0 ? (
                <section className="space-y-2">
                  <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
                    All findings ({findings.length})
                  </h3>
                  <ul className="space-y-2">
                    {findings.map((f) => {
                      const info = biomarkerInfo(f.biomarker_type);
                      return (
                        <li
                          key={f.id}
                          className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3 space-y-1"
                        >
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className={`inline-block rounded px-1.5 py-px text-[10px] uppercase font-semibold ${SEVERITY_CHIP[f.severity]}`}>
                              {SEVERITY_LABEL[f.severity]}
                            </span>
                            <span className="text-xs font-semibold text-[var(--color-text-primary)]">
                              {biomarkerLabel(f.biomarker_type)}
                            </span>
                            <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
                              {CATEGORY_LABEL[info.category]}
                            </span>
                            {f.function_name ? (() => {
                              const label = `${f.function_name}${f.line_start ? `:${f.line_start}` : ""}`;
                              const lineHref =
                                f.line_start != null && fileViewHrefFor
                                  ? fileViewHrefFor(f.line_start)
                                  : f.line_start != null
                                    ? fileViewHref
                                    : undefined;
                              return lineHref ? (
                                <a
                                  href={lineHref}
                                  className="text-xs font-mono text-[var(--color-accent-primary)] hover:underline"
                                >
                                  {label}
                                </a>
                              ) : (
                                <span className="text-xs font-mono text-[var(--color-text-tertiary)]">
                                  {label}
                                </span>
                              );
                            })() : null}
                            <span className="ml-auto text-xs tabular-nums text-red-500">−{f.health_impact.toFixed(2)}</span>
                          </div>
                          <p className="text-xs text-[var(--color-text-secondary)]">{f.reason}</p>
                          <BiomarkerDetails
                            biomarkerType={f.biomarker_type}
                            details={f.details}
                            onPartnerSelect={onPartnerSelect}
                            onPartnerHref={onPartnerHref}
                          />
                          {suggestions[f.biomarker_type] ? (
                            <p className="text-xs text-[var(--color-text-tertiary)] italic">
                              {suggestions[f.biomarker_type]}
                            </p>
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                </section>
              ) : null}
            </>
          )}
        </div>
      </aside>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-2.5">
      <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)] mb-0.5">
        {label}
      </p>
      <div className="text-[var(--color-text-primary)]">{value}</div>
    </div>
  );
}
