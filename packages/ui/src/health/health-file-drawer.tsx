"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, ExternalLink } from "lucide-react";
import { AdaptivePanel } from "../shared/adaptive-panel";
import { InfoTip } from "../shared/info-tip";
import {
  biomarkerLabel,
  biomarkerInfo,
  biomarkerDimension,
  CATEGORY_LABEL,
  DIMENSION_CHIP,
  DIMENSION_LABEL,
  type BiomarkerDimension,
} from "./biomarker-glossary";
import { BiomarkerDetails, type BiomarkerDetailsRecord } from "./biomarker-details";
import { ScoreBreakdown, type ScoreBreakdownCategory } from "./score-breakdown";
import { FileSignalsPanel } from "./file-signals-panel";
import { CollapsibleSection } from "../shared/collapsible-section";
import { formatRelativeTimeOrNull } from "../lib/format";
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
  /** Home pillar; falls back to the biomarker's glossary dimension. */
  dimension?: BiomarkerDimension | string;
}

export interface HealthDrawerMetric {
  file_path: string;
  score: number;
  /** Structural counters — null when the host has no metric row for the
   *  file, so the drawer can say "not measured" instead of a misleading 0. */
  max_ccn: number | null;
  max_nesting: number | null;
  nloc: number | null;
  module: string | null;
  duplication_pct?: number | null;
  line_coverage_pct?: number | null;
  has_test_file: boolean;
  /** Per-dimension scores from the three-signal split (null until populated). */
  defect_score?: number | null;
  maintainability_score?: number | null;
  performance_score?: number | null;
  /** Dominant-cause lead + pre-clamp deduction magnitude (null when absent). */
  primary_biomarker?: string | null;
  primary_reason?: string | null;
  total_deduction?: number | null;
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

  // A single finding row. Rendered inside a function group; kept as a closure
  // (not a component) so it reads the drawer's triage state without threading
  // it through props on every collapsible group.
  const renderFinding = (f: HealthDrawerFinding) => {
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
          {(() => {
            const dim =
              f.dimension === "maintainability" ||
              f.dimension === "defect" ||
              f.dimension === "performance"
                ? f.dimension
                : biomarkerDimension(f.biomarker_type);
            return (
              <span
                className={`inline-flex items-center rounded px-1.5 py-px text-[10px] font-medium ${DIMENSION_CHIP[dim]}`}
                title={`${DIMENSION_LABEL[dim]} pillar`}
              >
                {DIMENSION_LABEL[dim]}
              </span>
            );
          })()}
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
  };

  // Group findings by the function they fire on so one oversized function reads
  // as a single collapsible group instead of N sibling rows. File-level markers
  // (no function_name — co_change_scatter, change_entropy, …) collect into one
  // "File-level signals" group. Sections sort by summed impact so the dominant
  // cause leads; the worst section starts expanded.
  const findingSections = (() => {
    const groups = new Map<string, HealthDrawerFinding[]>();
    for (const f of findings) {
      const key = f.function_name ?? " file";
      const bucket = groups.get(key);
      if (bucket) bucket.push(f);
      else groups.set(key, [f]);
    }
    return [...groups.entries()]
      .map(([key, group]) => {
        const isFile = key === " file";
        const total = group.reduce((s, f) => s + f.health_impact, 0);
        const worst = group.reduce((a, b) => (b.health_impact > a.health_impact ? b : a));
        return { key, group, isFile, total, worst };
      })
      .sort((a, b) => b.total - a.total);
  })();

  // The one reason this file scores low: prefer the server lead, else the
  // worst finding. Rendered as a headline so the "why" leads (P3).
  const primaryLead = (() => {
    if (metric?.primary_biomarker) {
      return { biomarker: metric.primary_biomarker, reason: metric.primary_reason ?? null };
    }
    if (findings.length === 0) return null;
    const worst = findings.reduce((a, b) => (b.health_impact > a.health_impact ? b : a));
    return { biomarker: worst.biomarker_type, reason: worst.reason };
  })();

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
              No metric for this file yet. It appears after the next index or sync.
            </div>
          ) : (
            <>
              {primaryLead ? (
                <div className="rounded-md border-l-2 border-[var(--color-error)] bg-[var(--color-bg-elevated)] px-3 py-2">
                  <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
                    Leading cause
                  </p>
                  <p className="text-sm text-[var(--color-text-primary)]">
                    <span className="font-semibold">{biomarkerLabel(primaryLead.biomarker)}</span>
                    {primaryLead.reason ? (
                      <span className="text-[var(--color-text-secondary)]"> — {primaryLead.reason}</span>
                    ) : null}
                  </p>
                </div>
              ) : null}

              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <Stat label="Defect risk" value={
                  <span className={`inline-flex items-baseline rounded px-2 py-0.5 font-bold tabular-nums ${scoreBadgeClass(metric.score)}`}>
                    {metric.score.toFixed(1)}
                    <span className="ml-0.5 text-[10px] font-normal opacity-70">/10</span>
                  </span>
                } />
                <Stat label="Maintainability" value={
                  metric.maintainability_score == null ? (
                    <span className="text-xs text-[var(--color-text-tertiary)]">—</span>
                  ) : (
                    <span className={`inline-flex items-baseline rounded px-2 py-0.5 font-bold tabular-nums ${scoreBadgeClass(metric.maintainability_score)}`}>
                      {metric.maintainability_score.toFixed(1)}
                      <span className="ml-0.5 text-[10px] font-normal opacity-70">/10</span>
                    </span>
                  )
                } />
                <Stat label="Performance" value={
                  metric.performance_score == null ? (
                    <span className="text-xs text-[var(--color-text-tertiary)]">—</span>
                  ) : (
                    <span className={`inline-flex items-baseline rounded px-2 py-0.5 font-bold tabular-nums ${scoreBadgeClass(metric.performance_score)}`}>
                      {metric.performance_score.toFixed(1)}
                      <span className="ml-0.5 text-[10px] font-normal opacity-70">/10</span>
                    </span>
                  )
                } />
                <Stat label="Max CCN" value={<MeasuredNum v={metric.max_ccn} />} />
                <Stat label="Nest" value={<MeasuredNum v={metric.max_nesting} />} />
                <Stat label="NLOC" value={<MeasuredNum v={metric.nloc} />} />
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

              <BugHistorySection signals={signals} />

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
                  <div className="space-y-2">
                    {findingSections.map((s) => (
                      <FunctionFindingsGroup
                        key={s.key}
                        isFile={s.isFile}
                        functionName={s.isFile ? null : s.key}
                        findings={s.group}
                        total={s.total}
                        worst={s.worst}
                        // Single-marker groups have nothing to collapse; multi-
                        // marker groups start collapsed so the drawer opens as
                        // compact headers (the "padded" fix) — the leading-cause
                        // headline already surfaces the top reason.
                        defaultExpanded={s.group.length === 1}
                        renderFinding={renderFinding}
                      />
                    ))}
                  </div>
                </section>
              ) : null}
            </>
          )}
        </div>
    </AdaptivePanel>
  );
}

/**
 * One collapsible group of findings that fire on the same function (or the
 * "File-level signals" bucket when they have no function). The header names the
 * function plus its worst marker so a 7-marker oversized function reads as one
 * row, not seven — the P2 "looks padded" fix. Single-finding groups render
 * expanded; the caller expands the highest-impact group by default.
 */
/**
 * Which symbols this file's recent bug fixes landed in, behind a disclosure.
 *
 * Collapsed by default and silent without per-symbol data: the counts are a
 * "where do the bugs cluster" question, not something every reader of the
 * drawer needs answered. Two honesty rules show up in the copy. The heading
 * carries the last-fix age because a fix count without recency reads the same
 * at two weeks and two years. And the counts are labelled approximate, because
 * symbol spans are current-tree while each fix's line ranges are numbered on
 * its own parent commit, so a file that has moved since is matched on lines
 * that shifted.
 *
 * No commit is named here. File-level SZZ ran at 74.5% precision against the
 * frozen judgments, which is enough to count fixes and not enough to say which
 * commit caused one.
 */
function BugHistorySection({ signals }: { signals: FileSignals | null | undefined }) {
  const counts = signals?.fix_symbol_counts;
  if (!counts || Object.keys(counts).length === 0) return null;

  const lastFix = formatRelativeTimeOrNull(signals?.last_fix_at ?? null, "");
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);

  return (
    <CollapsibleSection
      title="Bug history"
      hint={lastFix ? `last fix ${lastFix}` : "last fix unknown"}
    >
      <ul className="space-y-1">
        {entries.map(([symbolId, count]) => (
          <li
            key={symbolId}
            className="flex items-baseline gap-2 text-xs text-[var(--color-text-secondary)]"
          >
            <code className="font-mono text-[var(--color-text-primary)]">
              {symbolId.split("::").pop()}
            </code>
            <span className="ml-auto tabular-nums text-[var(--color-text-tertiary)]">
              {count} {count === 1 ? "fix" : "fixes"}
            </span>
          </li>
        ))}
      </ul>
      <p className="text-[10px] leading-tight text-[var(--color-text-tertiary)]">
        Approximate: fixes are matched to symbols by line range, and lines move.
      </p>
    </CollapsibleSection>
  );
}

function FunctionFindingsGroup({
  isFile,
  functionName,
  findings,
  total,
  worst,
  defaultExpanded,
  renderFinding,
}: {
  isFile: boolean;
  functionName: string | null;
  findings: HealthDrawerFinding[];
  total: number;
  worst: HealthDrawerFinding;
  defaultExpanded: boolean;
  renderFinding: (f: HealthDrawerFinding) => React.ReactNode;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const toggle = () => setExpanded((e) => !e);
  const worstLabel = biomarkerLabel(worst.biomarker_type);
  return (
    <div className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]">
      <div
        role="button"
        tabIndex={0}
        onClick={toggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggle();
          }
        }}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2 px-3 py-2 text-left cursor-pointer rounded-t-md hover:bg-[var(--color-bg-surface)] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
      >
        {expanded ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-[var(--color-text-tertiary)]" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-[var(--color-text-tertiary)]" />
        )}
        {isFile ? (
          <span className="text-sm font-medium text-[var(--color-text-primary)]">
            File-level signals
          </span>
        ) : (
          <span className="min-w-0 truncate text-sm font-medium text-[var(--color-text-primary)]">
            <span className="font-mono">{functionName}</span>
            <span className="text-[var(--color-text-tertiary)]"> — {worstLabel}</span>
          </span>
        )}
        <span className="ml-auto inline-flex shrink-0 items-center gap-2 text-xs tabular-nums">
          <span className="text-[var(--color-text-tertiary)]">
            {findings.length} {findings.length === 1 ? "marker" : "markers"}
          </span>
          <span className="text-[var(--color-error)]">−{total.toFixed(2)}</span>
        </span>
      </div>
      {expanded ? (
        <ul className="space-y-2 border-t border-[var(--color-border-default)] p-2">
          {findings.map((f) => renderFinding(f))}
        </ul>
      ) : null}
    </div>
  );
}

/** A structural counter that may genuinely be unmeasured — say so instead of
 *  rendering a misleading 0. */
function MeasuredNum({ v }: { v: number | null }) {
  if (v == null) {
    return (
      <span
        className="text-xs text-[var(--color-text-tertiary)]"
        title="Not measured — no metric row is available for this file on this snapshot."
      >
        not measured
      </span>
    );
  }
  return <span className="text-base font-semibold tabular-nums">{v}</span>;
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
