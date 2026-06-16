"use client";

import { useState } from "react";
import { ExternalLink } from "lucide-react";
import { AdaptivePanel } from "../shared/adaptive-panel";
import { InfoTip } from "../shared/info-tip";
import { biomarkerLabel, biomarkerInfo, CATEGORY_LABEL } from "./biomarker-glossary";
import { BiomarkerDetails, type BiomarkerDetailsRecord } from "./biomarker-details";
import { ScoreBreakdown, type ScoreBreakdownCategory } from "./score-breakdown";
import { FileSignalsPanel } from "./file-signals-panel";
import { Sparkline } from "./sparkline";
import {
  SEVERITY_CHIP,
  SEVERITY_LABEL,
  deltaColor,
  formatDelta,
  scoreBadgeClass,
  type Severity,
} from "./tokens";
import type { FileHealthTrend, FileSignals } from "@repowise-dev/types/health";

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
  /** Per-file score trajectory; renders a compact sparkline when populated. */
  trend?: FileHealthTrend | null;
  /** Process / people / topology signals; the panel is silent when absent. */
  signals?: FileSignals | null;
  fileViewHref?: string;
  /** Build a per-line deep-link from the drawer's function:line span. */
  fileViewHrefFor?: ((lineStart: number) => string) | undefined;
  permalinkHref?: string;
  onPartnerSelect?: ((path: string) => void) | undefined;
  onPartnerHref?: ((path: string) => string) | undefined;
  /** Triage callback — PATCH the finding status. Buttons hide when absent. */
  onFindingStatusChange?:
    | ((findingId: string, status: string) => Promise<void> | void)
    | undefined;
}

const TRIAGE_STATUSES: { value: string; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "acknowledged", label: "Acknowledged" },
  { value: "resolved", label: "Resolved" },
  { value: "false_positive", label: "False positive" },
];

export function HealthFileDrawer({
  open,
  onClose,
  loading,
  metric,
  breakdown,
  findings = [],
  suggestions = {},
  trend,
  signals,
  fileViewHref,
  fileViewHrefFor,
  permalinkHref,
  onPartnerSelect,
  onPartnerHref,
  onFindingStatusChange,
}: HealthFileDrawerProps) {
  const [statusOverride, setStatusOverride] = useState<Record<string, string>>({});
  const [savingId, setSavingId] = useState<string | null>(null);
  const setStatus = async (id: string, status: string) => {
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
    <AdaptivePanel
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      eyebrow="File health"
      title={metric?.file_path ?? "Loading…"}
      widthClassName="md:max-w-[640px]"
    >
        <div className="px-4 py-4 space-y-5">
          {permalinkHref ? (
            <a
              href={permalinkHref}
              className="inline-flex items-center gap-1 text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
              title="Open as a shareable page"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              Open full page
            </a>
          ) : null}
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

              {trend && trend.points.length >= 2 ? (
                <div className="flex items-center gap-3 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-3 py-2">
                  <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
                    Trend
                  </span>
                  <Sparkline
                    values={trend.points.map((p) => p.score)}
                    domain={[0, 10]}
                    width={120}
                    height={28}
                    stroke="var(--color-accent-primary)"
                  />
                  {trend.delta != null && trend.delta !== 0 ? (
                    <span className={`text-xs font-semibold tabular-nums ${deltaColor(trend.delta)}`}>
                      {formatDelta(trend.delta)}
                    </span>
                  ) : null}
                  {trend.declining ? (
                    <span className="text-[10px] font-semibold uppercase tracking-wider text-[var(--color-error)]">
                      Declining
                    </span>
                  ) : null}
                </div>
              ) : null}

              <FileSignalsPanel signals={signals} />

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
                            <span className="inline-flex items-center gap-1 text-xs font-semibold text-[var(--color-text-primary)]">
                              {biomarkerLabel(f.biomarker_type)}
                              {info.description ? (
                                <InfoTip
                                  content={info.description}
                                  label={`About ${biomarkerLabel(f.biomarker_type)}`}
                                />
                              ) : null}
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
                            <span className="ml-auto text-xs tabular-nums text-[var(--color-error)]">−{f.health_impact.toFixed(2)}</span>
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
                          {onFindingStatusChange ? (
                            <div className="flex flex-wrap items-center gap-1.5 pt-1">
                              {TRIAGE_STATUSES.map((opt) => {
                                const current = statusOverride[f.id] ?? f.status ?? "open";
                                return (
                                  <button
                                    key={opt.value}
                                    type="button"
                                    disabled={savingId === f.id || current === opt.value}
                                    onClick={() => setStatus(f.id, opt.value)}
                                    className={`rounded border px-1.5 py-0.5 text-[10px] transition-colors ${
                                      current === opt.value
                                        ? "border-[var(--color-accent-primary)] text-[var(--color-accent-primary)]"
                                        : "border-[var(--color-border-default)] text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] hover:border-[var(--color-border-strong)]"
                                    }`}
                                  >
                                    {opt.label}
                                  </button>
                                );
                              })}
                            </div>
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
    </AdaptivePanel>
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
