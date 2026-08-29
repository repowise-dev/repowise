"use client";

import { useMemo, useState, type ReactNode } from "react";
import { HeartPulse } from "lucide-react";
import { bandForScore, HEALTH_BAND_LABEL } from "@repowise-dev/types/health";
import { EmptyState } from "../shared/empty-state";
import { VirtualizedTable, useVirtualRows } from "../shared/virtualized-table";
import { ScoreBreakdown, type ScoreBreakdownCategory } from "../health/score-breakdown";
import { BiomarkerDetails, type BiomarkerDetailsRecord } from "../health/biomarker-details";
import {
  biomarkerInfo,
  biomarkerLabel,
  biomarkerDimension,
  CATEGORY_LABEL,
  DIMENSION_LABEL,
  type BiomarkerDimension,
} from "../health/biomarker-glossary";
import { healthBandTextColor, type Severity } from "../health/tokens";
import { FileTrendChart } from "../health/file-trend-chart";
import { FileSignalsPanel } from "../health/file-signals-panel";
import { FindingOpportunityLink } from "../health/file-opportunity";
import { StatRibbon, type RibbonStat } from "../stats/stat-ribbon";
import { SeverityMark } from "../health/severity-mark";
import { formatNumber } from "../lib/format";
import type { FileDetailHealth, FunctionBlameRow } from "@repowise-dev/types/files";
import type { RefactoringOpportunity } from "@repowise-dev/types/refactoring";
import { FileSection, Fig } from "./file-section";

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
  /**
   * The file's composed refactoring opportunity. This is the surface a reader is
   * on once they have decided which file they care about, so it is the most
   * natural place to hand them the plan - and it was the one with no link at
   * all. Findings whose cause the opportunity does not address get none, so the
   * page never offers a plan for a problem the plan does not answer.
   */
  opportunity?: RefactoringOpportunity | null | undefined;
  refactoringOpportunityHref?: ((opportunityId: string) => string) | undefined;
}

/** Collapsed finding-card height guess; cards expand and are measured live. */
const FINDING_CARD_ESTIMATE = 120;
/** Blame table row height (compact `py-1.5` rows). */
const BLAME_ROW_ESTIMATE = 32;

function medianAgeDays(medianAuthorTime: number | null): number | null {
  if (!medianAuthorTime) return null;
  return Math.max(0, Math.round((Date.now() / 1000 - medianAuthorTime) / 86400));
}

/**
 * The one tab body that is genuinely interactive — finding triage, two
 * virtualised lists and a measured trend — and therefore the only one that
 * carries `"use client"`. Every other panel renders on the server and crosses
 * the boundary as markup rather than as data plus a bundle.
 */
export function FileHealthTab({
  health,
  functionBlame,
  onFindingStatusChange,
  partnerHref,
  symbolHref,
  opportunity,
  refactoringOpportunityHref,
}: FileHealthTabProps) {
  const [statusOverride, setStatusOverride] = useState<Record<string, FindingStatus>>({});
  const [savingId, setSavingId] = useState<string | null>(null);
  const { metric, breakdown, findings, trend, signals } = health;

  // Window the findings card list. Cards expand, so this is variable-height —
  // FINDING_CARD_ESTIMATE is the collapsed-card guess; real heights are measured.
  const findingsVirtual = useVirtualRows<HTMLUListElement>({
    count: findings.length,
    estimateSize: FINDING_CARD_ESTIMATE,
  });

  // Hoist medianAgeDays + the row-name JSX out of the blame render loop so they
  // run once per dataset change instead of on every render.
  const blameRows = useMemo(
    () =>
      functionBlame.map((row) => ({
        row,
        age: medianAgeDays(row.median_author_time),
        name: (
          <span className="font-mono">
            {row.function_name}
            <span className="text-[var(--color-text-tertiary)]">:{row.start_line}</span>
          </span>
        ) as ReactNode,
      })),
    [functionBlame],
  );

  // `functionBlame` is part of the test: a file git tracks but the parser never
  // scored has blame rows and no metric, and the early return dropped the
  // churn table on the floor for it.
  if (!metric && findings.length === 0 && functionBlame.length === 0) {
    return (
      <EmptyState
        titleAs="h2"
        icon={<HeartPulse className="h-8 w-8" />}
        title="No health data"
        description="Scores, biomarkers and the per-function churn table land with the first index of this repository."
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

  const pillars: RibbonStat[] = [];
  if (metric) {
    // Defect risk is deliberately absent: the header's lede carries it at 44px
    // with its band, on screen from whichever tab you arrive on. Repeating it
    // here at a quarter the size is the same number twice.
    if (metric.maintainability_score != null) {
      pillars.push({
        label: "Maintainability",
        value: metric.maintainability_score.toFixed(1),
        valueColor: healthBandTextColor(bandForScore(metric.maintainability_score)),
        sub: HEALTH_BAND_LABEL[bandForScore(metric.maintainability_score)],
      });
    }
    if (metric.performance_score != null) {
      pillars.push({
        label: "Performance",
        value: metric.performance_score.toFixed(1),
        valueColor: healthBandTextColor(bandForScore(metric.performance_score)),
        sub: HEALTH_BAND_LABEL[bandForScore(metric.performance_score)],
      });
    }
    pillars.push({ label: "Max CCN", value: formatNumber(metric.max_ccn) });
    pillars.push({ label: "Max nesting", value: formatNumber(metric.max_nesting) });
    if (metric.duplication_pct != null) {
      pillars.push({ label: "Duplication", value: `${metric.duplication_pct.toFixed(0)}%` });
    }
  }

  return (
    <div>
      {metric && (
        <FileSection
          first
          title="The three signals"
          description={
            <>
              Defect risk is the calibrated number in the header. Maintainability and performance
              are co-equal signals rather than a blend of it, and they are banded the same way —
              healthy at 8 and above, alert below 4.{" "}
              {metric.has_test_file
                ? "This file has a paired test file."
                : "No paired test file was found for it."}
              {metric.module && (
                <>
                  {" "}
                  It sits in <Fig>{metric.module}</Fig>.
                </>
              )}
            </>
          }
        >
          {pillars.length > 0 && <StatRibbon stats={pillars} />}
          {trend && trend.points.length >= 2 && <FileTrendChart trend={trend} />}
        </FileSection>
      )}

      {(breakdown || signals) && (
        <FileSection
          first={!metric}
          title="Why this score"
          description="Every deduction that separates this file from a clean 10, and the repository-level signals that fed them."
        >
          {breakdown && (
            <ScoreBreakdown
              score={breakdown.score}
              totalDeduction={breakdown.total_deduction}
              categories={breakdown.categories as ScoreBreakdownCategory[]}
            />
          )}
          <FileSignalsPanel signals={signals} />
        </FileSection>
      )}

      {findings.length > 0 && (
        <FileSection
          first={!metric && !breakdown && !signals}
          title="Findings"
          description={
            <>
              <Fig>{formatNumber(findings.length)}</Fig> open{" "}
              {findings.length === 1 ? "biomarker" : "biomarkers"}, worst first. The figure on the
              right of each row is what it costs the score.
            </>
          }
        >
          <ul
            ref={findingsVirtual.scrollRef}
            className="divide-y divide-[var(--color-border-default)] overflow-auto border-y border-[var(--color-border-default)]"
            style={{ maxHeight: 600 }}
          >
            {findingsVirtual.paddingTop > 0 && (
              <li aria-hidden style={{ height: findingsVirtual.paddingTop }} />
            )}
            {findingsVirtual.virtualRows.map((vr) => {
              const f = findings[vr.index];
              if (f === undefined) return null;
              const info = biomarkerInfo(f.biomarker_type);
              const status = statusOverride[f.id] ?? (f.status as FindingStatus) ?? "open";
              return (
                <li
                  key={f.id}
                  ref={findingsVirtual.measureElement}
                  data-index={vr.index}
                  className="space-y-1.5 py-3"
                >
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <SeverityMark severity={f.severity as Severity} />
                    <span className="text-sm font-semibold text-[var(--color-text-primary)]">
                      {biomarkerLabel(f.biomarker_type)}
                    </span>
                    {/* Category and pillar are machine-produced labels, so they
                        are mono micro-labels rather than two tinted chips —
                        rule 9, nothing here responds to a click. */}
                    <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                      {CATEGORY_LABEL[info.category]} · {DIMENSION_LABEL[findingDimension(f)]}
                    </span>
                    {f.function_name && (
                      <span className="font-mono text-xs text-[var(--color-text-tertiary)]">
                        {f.function_name}
                        {f.line_start ? `:${f.line_start}` : ""}
                      </span>
                    )}
                    <span className="ml-auto font-mono text-xs tabular-nums text-[var(--color-text-secondary)]">
                      &minus;{f.health_impact.toFixed(2)}
                    </span>
                  </div>
                  <p className="text-sm leading-relaxed text-[var(--color-text-secondary)]">
                    {f.reason}
                  </p>
                  <BiomarkerDetails
                    biomarkerType={f.biomarker_type}
                    details={f.details as BiomarkerDetailsRecord | null}
                    onPartnerHref={partnerHref}
                  />
                  <FindingOpportunityLink
                    opportunity={opportunity}
                    biomarkerType={f.biomarker_type}
                    href={refactoringOpportunityHref}
                  />
                  {onFindingStatusChange && (
                    // One control, four states — a segmented selector for a
                    // single field, not rule 14's pile of independent verbs
                    // (which mixed "change this record" with "navigate away").
                    <div className="flex flex-wrap items-center gap-1 pt-1">
                      {STATUS_OPTIONS.map((opt) => (
                        <button
                          key={opt.value}
                          type="button"
                          disabled={savingId === f.id || status === opt.value}
                          onClick={() => setStatus(f.id, opt.value)}
                          className={`rounded px-2 py-0.5 text-xs font-medium transition-colors ${
                            status === opt.value
                              ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
                              : "text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
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
            {findingsVirtual.paddingBottom > 0 && (
              <li aria-hidden style={{ height: findingsVirtual.paddingBottom }} />
            )}
          </ul>
        </FileSection>
      )}

      {functionBlame.length > 0 && (
        <FileSection
          first={!metric && !breakdown && !signals && findings.length === 0}
          title="Functions by churn"
          description={
            <>
              How often each function in this file has been modified, and who owns the lines.
              Median age is how long the current lines have been standing.
            </>
          }
        >
          <VirtualizedTable
            rows={blameRows}
            rowKey={(b) => b.row.symbol_id}
            estimateRowHeight={BLAME_ROW_ESTIMATE}
            className="overflow-x-auto overflow-hidden border-y border-[var(--color-border-default)]"
            tableClassName="text-sm"
            header={
              <tr className="border-b border-[var(--color-border-default)] text-left font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                <th className="px-3 py-2 font-normal">Function</th>
                <th className="px-3 py-2 text-right font-normal">Mods</th>
                <th className="px-3 py-2 text-right font-normal">Recent</th>
                <th className="px-3 py-2 text-right font-normal">Median age</th>
                <th className="px-3 py-2 font-normal">Owner</th>
              </tr>
            }
            renderRow={({ row: b, age, name }, index, measureRef) => (
              <tr
                ref={measureRef}
                data-index={index}
                className="border-b border-[var(--color-table-divider)] last:border-0 hover:bg-[var(--color-bg-elevated)]"
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
            )}
          />
        </FileSection>
      )}
    </div>
  );
}

/** A finding's home pillar, preferring the server value over the glossary. */
function findingDimension(f: { dimension?: string; biomarker_type: string }): BiomarkerDimension {
  if (
    f.dimension === "defect" ||
    f.dimension === "maintainability" ||
    f.dimension === "performance"
  ) {
    return f.dimension;
  }
  return biomarkerDimension(f.biomarker_type);
}
