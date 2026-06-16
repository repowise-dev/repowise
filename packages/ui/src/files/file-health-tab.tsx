"use client";

import { useState } from "react";
import { HeartPulse } from "lucide-react";
import { EmptyState } from "../shared/empty-state";
import { ScoreBreakdown, type ScoreBreakdownCategory } from "../health/score-breakdown";
import { BiomarkerDetails, type BiomarkerDetailsRecord } from "../health/biomarker-details";
import { biomarkerInfo, biomarkerLabel, CATEGORY_LABEL } from "../health/biomarker-glossary";
import { SEVERITY_CHIP, SEVERITY_LABEL, scoreBadgeClass, type Severity } from "../health/tokens";
import { FileTrendChart } from "../health/file-trend-chart";
import type { FileDetailHealth, FunctionBlameRow } from "@repowise-dev/types/files";

export type FindingStatus = "open" | "acknowledged" | "resolved" | "false_positive";

const STATUS_OPTIONS: { value: FindingStatus; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "acknowledged", label: "Acknowledged" },
  { value: "resolved", label: "Resolved" },
  { value: "false_positive", label: "False positive" },
];

interface FileHealthTabProps {
  health: FileDetailHealth;
  functionBlame: FunctionBlameRow[];
  /** Triage callback — PATCH the finding status. Buttons hide when absent. */
  onFindingStatusChange?:
    | ((findingId: string, status: FindingStatus) => Promise<void> | void)
    | undefined;
  /** Build an href for a co-change partner file (hidden-coupling details). */
  partnerHref?: ((path: string) => string) | undefined;
  /** Build a symbol-page href for a function row. */
  symbolHref?: ((symbolId: string) => string) | undefined;
}

function medianAgeDays(medianAuthorTime: number | null): number | null {
  if (!medianAuthorTime) return null;
  return Math.max(0, Math.round((Date.now() / 1000 - medianAuthorTime) / 86400));
}

export function FileHealthTab({
  health,
  functionBlame,
  onFindingStatusChange,
  partnerHref,
  symbolHref,
}: FileHealthTabProps) {
  const [statusOverride, setStatusOverride] = useState<Record<string, FindingStatus>>({});
  const [savingId, setSavingId] = useState<string | null>(null);
  const { metric, breakdown, findings, trend } = health;

  if (!metric && findings.length === 0) {
    return (
      <EmptyState
        icon={<HeartPulse className="h-8 w-8" />}
        title="No health data"
        description="Run repowise init (or repowise health) to score this file."
      />
    );
  }

  const setStatus = async (id: string, status: FindingStatus) => {
    if (!onFindingStatusChange) return;
    setSavingId(id);
    try {
      await onFindingStatusChange(id, status);
      setStatusOverride((m) => ({ ...m, [id]: status }));
    } finally {
      setSavingId(null);
    }
  };

  return (
    <div className="space-y-5">
      {metric && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat
            label="Score"
            value={
              <span
                className={`inline-flex items-baseline rounded px-2 py-0.5 font-bold tabular-nums ${scoreBadgeClass(metric.score)}`}
              >
                {metric.score.toFixed(1)}
                <span className="ml-0.5 text-[10px] font-normal opacity-70">/10</span>
              </span>
            }
          />
          <Stat label="Max CCN" value={<Num v={metric.max_ccn} />} />
          <Stat label="Max nesting" value={<Num v={metric.max_nesting} />} />
          <Stat label="NLOC" value={<Num v={metric.nloc} />} />
          <Stat label="Tests" value={<span className="text-xs">{metric.has_test_file ? "Paired" : "None"}</span>} />
          <Stat
            label="Coverage"
            value={
              <span className="text-xs tabular-nums">
                {metric.line_coverage_pct == null ? "—" : `${metric.line_coverage_pct.toFixed(0)}%`}
              </span>
            }
          />
          <Stat
            label="Duplication"
            value={
              <span className="text-xs tabular-nums">
                {metric.duplication_pct == null ? "—" : `${metric.duplication_pct.toFixed(0)}%`}
              </span>
            }
          />
          <Stat label="Module" value={<span className="text-xs">{metric.module ?? "—"}</span>} />
        </div>
      )}

      {trend && trend.points.length >= 2 && <FileTrendChart trend={trend} />}

      {breakdown && (
        <section className="space-y-2">
          <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
            Why this score?
          </h3>
          <ScoreBreakdown
            score={breakdown.score}
            totalDeduction={breakdown.total_deduction}
            categories={breakdown.categories as ScoreBreakdownCategory[]}
          />
        </section>
      )}

      {findings.length > 0 && (
        <section className="space-y-2">
          <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
            Findings ({findings.length})
          </h3>
          <ul className="space-y-2">
            {findings.map((f) => {
              const info = biomarkerInfo(f.biomarker_type);
              const status = statusOverride[f.id] ?? (f.status as FindingStatus) ?? "open";
              return (
                <li
                  key={f.id}
                  className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3 space-y-1"
                >
                  <div className="flex items-center gap-2 flex-wrap">
                    <span
                      className={`inline-block rounded px-1.5 py-px text-[10px] uppercase font-semibold ${SEVERITY_CHIP[f.severity as Severity]}`}
                    >
                      {SEVERITY_LABEL[f.severity as Severity]}
                    </span>
                    <span className="text-xs font-semibold text-[var(--color-text-primary)]">
                      {biomarkerLabel(f.biomarker_type)}
                    </span>
                    <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
                      {CATEGORY_LABEL[info.category]}
                    </span>
                    {f.function_name && (
                      <span className="text-xs font-mono text-[var(--color-text-tertiary)]">
                        {f.function_name}
                        {f.line_start ? `:${f.line_start}` : ""}
                      </span>
                    )}
                    <span className="ml-auto text-xs tabular-nums text-[var(--color-error)]">
                      −{f.health_impact.toFixed(2)}
                    </span>
                  </div>
                  <p className="text-xs text-[var(--color-text-secondary)]">{f.reason}</p>
                  <BiomarkerDetails
                    biomarkerType={f.biomarker_type}
                    details={f.details as BiomarkerDetailsRecord | null}
                    onPartnerHref={partnerHref}
                  />
                  {onFindingStatusChange && (
                    <div className="flex items-center gap-1.5 pt-1">
                      {STATUS_OPTIONS.map((opt) => (
                        <button
                          key={opt.value}
                          type="button"
                          disabled={savingId === f.id || status === opt.value}
                          onClick={() => setStatus(f.id, opt.value)}
                          className={`rounded border px-1.5 py-0.5 text-[10px] transition-colors ${
                            status === opt.value
                              ? "border-[var(--color-accent-primary)] text-[var(--color-accent-primary)]"
                              : "border-[var(--color-border-default)] text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] hover:border-[var(--color-border-strong)]"
                          }`}
                        >
                          {opt.label}
                        </button>
                      ))}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {functionBlame.length > 0 && (
        <section className="space-y-2">
          <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
            Functions by churn
          </h3>
          <div className="overflow-x-auto rounded-md border border-[var(--color-border-default)]">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-[var(--color-border-default)] text-left text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
                  <th className="px-3 py-2 font-medium">Function</th>
                  <th className="px-3 py-2 font-medium text-right">Mods</th>
                  <th className="px-3 py-2 font-medium text-right">Recent</th>
                  <th className="px-3 py-2 font-medium text-right">Median age</th>
                  <th className="px-3 py-2 font-medium">Owner</th>
                </tr>
              </thead>
              <tbody>
                {functionBlame.map((b) => {
                  const age = medianAgeDays(b.median_author_time);
                  const name = (
                    <span className="font-mono">
                      {b.function_name}
                      <span className="text-[var(--color-text-tertiary)]">:{b.start_line}</span>
                    </span>
                  );
                  return (
                    <tr
                      key={b.symbol_id}
                      className="border-b border-[var(--color-border-default)] last:border-0"
                    >
                      <td className="px-3 py-1.5 text-[var(--color-text-primary)]">
                        {symbolHref ? (
                          <a
                            href={symbolHref(b.symbol_id)}
                            className="hover:text-[var(--color-accent-primary)] hover:underline"
                          >
                            {name}
                          </a>
                        ) : (
                          name
                        )}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{b.mod_count}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{b.recent_mod_count}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        {age == null ? "—" : `${age}d`}
                      </td>
                      <td className="px-3 py-1.5 text-[var(--color-text-secondary)]">
                        {b.owner_name ?? "—"}
                        {b.owner_line_pct != null && (
                          <span className="text-[var(--color-text-tertiary)]">
                            {" "}
                            ({Math.round(b.owner_line_pct * 100)}%)
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}
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

function Num({ v }: { v: number }) {
  return <span className="text-base font-semibold tabular-nums">{v}</span>;
}
