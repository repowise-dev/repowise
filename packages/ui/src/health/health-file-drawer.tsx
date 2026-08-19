"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, ExternalLink } from "lucide-react";
import { AdaptivePanel } from "../shared/adaptive-panel";
import { InfoTip } from "../shared/info-tip";
import {
  biomarkerLabel,
  biomarkerInfo,
  biomarkerDimension,
  CATEGORY_CAP,
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
  type Severity,
} from "./tokens";
// Shared band function, never a local threshold: two surfaces disagreeing
// about where "Good" starts is worse than the import.
import { healthBand } from "../overview/health-lede";
import type { FileHealthTrend, FileSignals } from "@repowise-dev/types/health";
import { SeverityMark } from "./severity-mark";

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

/** Bucket for one-off file-level markers, kept pooled so a file with several
 *  distinct singletons still reads as one collapsed row. Not a biomarker name,
 *  and never compared against one. */
const POOLED_FILE_KEY = "__file_level__";

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
      // A hairline row, not a card inside a card. These sat as bordered boxes
      // inside a bordered group inside the drawer: three frames deep for one
      // marker.
      <li
        key={f.id}
        className="space-y-1 border-t border-[var(--color-border-default)] px-3 py-2.5 first:border-t-0"
      >
        <div className="flex items-center gap-2 flex-wrap">
          <SeverityMark severity={f.severity} />
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
          {/* Anchor on line_start, not function_name. Gating the whole anchor on
              the function dropped the line for every file-level marker that has
              one — `error_handling` fires per occurrence with a precise line and
              no function, so 34 markers on one file rendered as 34 rows whose
              only distinguishing field was the one being withheld. That is what
              made them read as duplicates. */}
          {f.function_name || f.line_start != null ? (() => {
            const label = f.function_name
              ? `${f.function_name}${f.line_start != null ? `:${f.line_start}` : ""}`
              : `line ${f.line_start}`;
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
                      : "border-[var(--color-border-default)] text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] hover:border-[var(--color-border-hover)]"
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

  // Categories the scorer held at their ceiling, per the server breakdown.
  // `capped` is the server's verdict and is never recomputed here. The cap
  // VALUE is only ever displayed, so it falls back to the glossary mirror the
  // way `score-breakdown.tsx` does — older payloads carry `capped` without
  // `cap`, and losing the chip over a missing display number helps nobody.
  const cappedCategories = new Map<string, number | null>();
  for (const c of breakdown?.categories ?? []) {
    if (c.capped) cappedCategories.set(String(c.category), c.cap ?? null);
  }

  // Group findings by the function they fire on, so one oversized function
  // reads as a single collapsible group instead of N sibling rows.
  //
  // Markers with no function used to pool into one "File-level signals" list,
  // which defeated that fix for the biomarker that needs it most:
  // `error_handling` sets no function_name and fires once per occurrence, so a
  // single file in this repo reaches 34 sibling rows repeating one 12-word
  // reason — worth 0.51 points, because the category caps at 0.5.
  //
  // So a file-level biomarker gets its own group only when it actually repeats.
  // Splitting *every* file-level biomarker out was measurably worse: singleton
  // groups render expanded, and 53% of files carry 2+ distinct one-off
  // file-level markers (`dry_violation` alone fires exactly once on ~1,500
  // files), so it turned one collapsed row into several expanded ones. The
  // one-offs stay pooled; only the floods split.
  const findingSections = (() => {
    const byFunction = new Map<string, HealthDrawerFinding[]>();
    const byBiomarker = new Map<string, HealthDrawerFinding[]>();
    for (const f of findings) {
      // Two separate maps, never one keyed by a sentinel string: a C++ or Rust
      // function_name can legitimately be `file::read`, so any "file" prefix is
      // collidable and would silently strip that function's name from its
      // header.
      const map = f.function_name ? byFunction : byBiomarker;
      const key = f.function_name ?? f.biomarker_type;
      const bucket = map.get(key);
      if (bucket) bucket.push(f);
      else map.set(key, [f]);
    }

    const pooled: HealthDrawerFinding[] = [];
    const fileGroups: { key: string; group: HealthDrawerFinding[] }[] = [];
    for (const [key, group] of byBiomarker) {
      if (group.length > 1) fileGroups.push({ key, group });
      else pooled.push(...group);
    }
    if (pooled.length > 0) fileGroups.push({ key: POOLED_FILE_KEY, group: pooled });

    const sections = [
      ...[...byFunction.entries()].map(([key, group]) => ({ key, group, isFile: false })),
      ...fileGroups.map(({ key, group }) => ({ key, group, isFile: true })),
    ];

    return sections
      .map(({ key, group, isFile }) => {
        // Inside a single-biomarker group every marker carries the same label
        // and the same impact, so impact cannot order them and the line is the
        // only axis a reader can follow. Unlined markers sort last.
        if (isFile && key !== POOLED_FILE_KEY) {
          group.sort((a, b) => (a.line_start ?? Infinity) - (b.line_start ?? Infinity));
        }
        const total = group.reduce((s, f) => s + f.health_impact, 0);
        const worst = group.reduce((a, b) => (b.health_impact > a.health_impact ? b : a));
        // Claim the cap only when this group IS the category — otherwise the
        // ceiling belongs to markers outside the group and naming it here
        // misattributes it. Decided from the findings on screen rather than by
        // matching the server's subtotal, because a host may scope the two
        // differently (hosted excludes triaged findings from the breakdown but
        // not from this list) and a float comparison would then silently fail.
        const cat = biomarkerInfo(worst.biomarker_type).category;
        const ownsCategory =
          key !== POOLED_FILE_KEY &&
          findings.every(
            (f) =>
              biomarkerInfo(f.biomarker_type).category !== cat ||
              f.biomarker_type === worst.biomarker_type,
          );
        const atCap =
          isFile && ownsCategory && cappedCategories.has(cat)
            ? (cappedCategories.get(cat) ?? CATEGORY_CAP[cat] ?? null)
            : null;
        return { key, group, isFile, total, worst, atCap };
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
        <div className="flex flex-col gap-6 px-4 py-4">
          {loading ? (
            <div className="text-sm text-[var(--color-text-tertiary)]">Loading…</div>
          ) : !metric ? (
            <p className="text-sm text-[var(--color-text-secondary)]">
              No metric for this file yet. It appears after the next index or sync.
            </p>
          ) : (
            <>
              {/* Lede: the score leads, and the leading cause is the sentence
                  that makes it mean something. This used to be a small chip
                  among ten identical bordered tiles, with the "why" in a
                  separate box above them. */}
              <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:gap-6">
                <div className="flex shrink-0 flex-col gap-2 sm:w-[150px]">
                  <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                    Defect risk
                  </p>
                  <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
                    <span
                      className="text-[40px] font-semibold leading-none tracking-tight tabular-nums"
                      style={{ color: healthBand(metric.score).color }}
                    >
                      {metric.score.toFixed(1)}
                    </span>
                    <span className="text-xs text-[var(--color-text-tertiary)]">out of 10</span>
                  </div>
                  <span
                    className="w-fit rounded-full border px-2.5 py-0.5 text-[11px] font-medium"
                    style={{
                      color: healthBand(metric.score).color,
                      borderColor: `color-mix(in srgb, ${healthBand(metric.score).color} 40%, transparent)`,
                      background: `color-mix(in srgb, ${healthBand(metric.score).color} 9%, transparent)`,
                    }}
                  >
                    {healthBand(metric.score).label}
                  </span>

                  {trend && trend.points.length >= 2 ? (
                    <div className="mt-1 flex items-center gap-2">
                      <Sparkline
                        values={trend.points.map((p) => p.score)}
                        domain={[0, 10]}
                        width={92}
                        height={24}
                        stroke="var(--color-accent-primary)"
                      />
                      {trend.delta != null && trend.delta !== 0 ? (
                        <span
                          className={`text-xs font-semibold tabular-nums ${deltaColor(trend.delta)}`}
                        >
                          {formatDelta(trend.delta)}
                        </span>
                      ) : null}
                      {trend.declining ? (
                        <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-error)]">
                          Declining
                        </span>
                      ) : null}
                    </div>
                  ) : null}
                </div>

                <div className="flex min-w-0 flex-col gap-3">
                  {primaryLead ? (
                    <div className="flex flex-col gap-1">
                      <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                        Leading cause
                      </p>
                      <p className="text-[13px] leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
                        <strong className="font-semibold text-[var(--color-text-primary)]">
                          {biomarkerLabel(primaryLead.biomarker)}.
                        </strong>
                        {primaryLead.reason ? ` ${primaryLead.reason}` : ""}
                      </p>
                    </div>
                  ) : null}

                  {/* The one action. It was two links to the same page, one a
                      tertiary line at the top and one accent-coloured in the
                      middle of the body. */}
                  {(permalinkHref ?? fileViewHref) ? (
                    <a
                      href={permalinkHref ?? fileViewHref}
                      className="inline-flex w-fit items-center gap-1.5 text-sm font-medium text-[var(--color-accent-primary)] hover:underline"
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                      Open full page
                    </a>
                  ) : null}
                </div>
              </div>

              {/* The other two pillars and the structural counters, as a
                  hairline list. Ten bordered tiles made a 3.1 and a 14 read as
                  the same kind of news. */}
              <MetricGrid metric={metric} />

              <FileSignalsPanel signals={signals} />

              <BugHistorySection signals={signals} />

              {breakdown ? (
                <section className="flex flex-col gap-2">
                  <h3 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                    Why this score
                  </h3>
                  <ScoreBreakdown
                    score={breakdown.score}
                    totalDeduction={breakdown.total_deduction}
                    categories={breakdown.categories}
                  />
                </section>
              ) : null}

              {findings.length > 0 ? (
                <section className="flex flex-col gap-2">
                  <h3 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                    All findings ({findings.length})
                  </h3>
                  <div className="flex flex-col">
                    {findingSections.map((s) => (
                      <FunctionFindingsGroup
                        key={s.isFile ? `file:${s.key}` : `fn:${s.key}`}
                        isFile={s.isFile}
                        pooled={s.key === POOLED_FILE_KEY}
                        functionName={s.isFile ? null : s.key}
                        findings={s.group}
                        total={s.total}
                        worst={s.worst}
                        atCap={s.atCap}
                        // Single-marker groups have nothing to collapse; multi-
                        // marker groups start collapsed so the drawer opens as
                        // compact headers, since the leading-cause line above
                        // already surfaces the top reason.
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
 * One collapsible group of findings that fire on the same function, or — for
 * markers with no function — on the same biomarker. The header names the
 * function plus its worst marker so a 7-marker oversized function reads as one
 * row, not seven — the P2 "looks padded" fix. A file-level group leads with the
 * biomarker instead, and carries a `capped` chip when the scorer is holding
 * that category at its ceiling. Single-finding groups render expanded.
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
  pooled,
  functionName,
  findings,
  total,
  worst,
  atCap,
  defaultExpanded,
  renderFinding,
}: {
  isFile: boolean;
  /** The mixed bucket of one-off file-level markers, not a single biomarker. */
  pooled: boolean;
  functionName: string | null;
  findings: HealthDrawerFinding[];
  total: number;
  worst: HealthDrawerFinding;
  /** The enforced cap when this group's category is holding at it, else null. */
  atCap: number | null;
  defaultExpanded: boolean;
  renderFinding: (f: HealthDrawerFinding) => React.ReactNode;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const toggle = () => setExpanded((e) => !e);
  const worstLabel = biomarkerLabel(worst.biomarker_type);
  return (
    <div className="border-t border-[var(--color-border-default)]">
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
        className="flex w-full cursor-pointer items-center gap-2 px-3 py-2.5 text-left transition-colors hover:bg-[var(--color-bg-surface)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
      >
        {expanded ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-[var(--color-text-tertiary)]" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-[var(--color-text-tertiary)]" />
        )}
        {isFile ? (
          <span className="min-w-0 truncate text-sm font-medium text-[var(--color-text-primary)]">
            {pooled ? (
              "File-level signals"
            ) : (
              <>
                {worstLabel}
                <span className="text-[var(--color-text-tertiary)]"> · file-level</span>
              </>
            )}
          </span>
        ) : (
          <span className="min-w-0 truncate text-sm font-medium text-[var(--color-text-primary)]">
            <span className="font-mono">{functionName}</span>
            <span className="text-[var(--color-text-tertiary)]"> · {worstLabel}</span>
          </span>
        )}
        <span className="ml-auto inline-flex shrink-0 items-center gap-2 text-xs tabular-nums">
          <span className="text-[var(--color-text-tertiary)]">
            {findings.length} {findings.length === 1 ? "marker" : "markers"}
          </span>
          <span className="text-[var(--color-error)]">−{total.toFixed(2)}</span>
          {/* Why 34 markers cost half a point. Without this the count reads as
              the severity and the capped total looks like a bug. The
              explanation rides in the accessible name, not a `title`: a bare
              `title` on a span with its own text never reaches the accessible
              name, so it would be mouse-only. */}
          {atCap !== null ? (
            <span
              className="rounded-sm bg-[var(--color-bg-surface)] px-1 py-px text-[10px] font-normal text-[var(--color-text-tertiary)]"
              aria-label={`capped: held at its ${atCap.toFixed(2)}-point ceiling, so further markers of this kind add no deduction`}
            >
              capped
            </span>
          ) : null}
        </span>
      </div>
      {expanded ? (
        <ul className="border-t border-[var(--color-border-default)] bg-[var(--color-bg-surface)]">
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

/**
 * The two co-pillars and the structural counters, as one hairline `<dl>`.
 *
 * Was ten bordered tiles in a 4-column grid that wrapped to 4/4/2 and gave a
 * pillar score, a cyclomatic count and a module name identical weight. Scores
 * carry their band colour; counters are plain, because a nesting depth of 3 is
 * not good or bad news on its own.
 */
function MetricGrid({ metric }: { metric: HealthDrawerMetric }) {
  const cells: { label: string; value: React.ReactNode }[] = [
    {
      label: "Maintainability",
      value: <PillarScore v={metric.maintainability_score ?? null} />,
    },
    { label: "Performance", value: <PillarScore v={metric.performance_score ?? null} /> },
    {
      // Stays "Coverage" while the tab is renamed to "Tests": this cell is the
      // measured percentage, and a "Tests" cell already sits four rows down
      // carrying the paired-file flag. Two cells named the same thing in one
      // grid is the vocabulary failure the rename exists to avoid.
      label: "Coverage",
      value: (
        <PlainValue>
          {metric.line_coverage_pct == null
            ? "not measured"
            : `${metric.line_coverage_pct.toFixed(0)}%`}
        </PlainValue>
      ),
    },
    { label: "Max CCN", value: <MeasuredNum v={metric.max_ccn} /> },
    { label: "Nesting", value: <MeasuredNum v={metric.max_nesting} /> },
    { label: "NLOC", value: <MeasuredNum v={metric.nloc} /> },
    {
      label: "Duplication",
      value: (
        <PlainValue>
          {metric.duplication_pct == null
            ? "not measured"
            : `${metric.duplication_pct.toFixed(0)}%`}
        </PlainValue>
      ),
    },
    {
      label: "Tests",
      value: <PlainValue>{metric.has_test_file ? "Paired" : "None"}</PlainValue>,
    },
    {
      label: "Module",
      value: (
        <span
          className="block truncate font-mono text-sm text-[var(--color-text-primary)]"
          title={metric.module ?? undefined}
        >
          {metric.module ?? "none"}
        </span>
      ),
    },
  ];

  return (
    <dl className="grid grid-cols-2 border-y border-[var(--color-border-default)] sm:grid-cols-3">
      {cells.map((c, i) => (
        <div
          key={c.label}
          className={[
            "min-w-0 px-3 py-2.5",
            "border-[var(--color-border-default)]",
            // Hairlines between cells only; the outer edges come from border-y
            // on the wrapper, so cells never double up on a boundary.
            i % 2 === 1 ? "border-l" : "",
            i >= 2 ? "border-t" : "",
            "sm:border-l sm:border-t-0",
            i % 3 === 0 ? "sm:border-l-0" : "",
            i >= 3 ? "sm:border-t" : "",
          ]
            .filter(Boolean)
            .join(" ")}
        >
          <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
            {c.label}
          </dt>
          <dd className="mt-1">{c.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** A 0–10 pillar score, band-coloured, or an honest "not measured". */
function PillarScore({ v }: { v: number | null }) {
  if (v == null) {
    return <span className="text-sm text-[var(--color-text-tertiary)]">not measured</span>;
  }
  return (
    <span
      className="text-lg font-semibold tabular-nums"
      style={{ color: healthBand(v).color }}
    >
      {v.toFixed(1)}
      <span className="text-xs font-normal text-[var(--color-text-tertiary)]">/10</span>
    </span>
  );
}

function PlainValue({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-sm tabular-nums text-[var(--color-text-primary)]">{children}</span>
  );
}
