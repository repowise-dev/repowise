/**
 * The Coverage tab's opening read: one figure, the sentences that make it mean
 * something, and the operational facts as a hairline ribbon.
 *
 * It replaces five `MetricCard`s in a grid. Between them they carried the
 * headline percentage, two line counts, the report format and its ingest time
 * at identical weight behind five uppercase labels, which is the box-soup
 * failure: nothing leads, and the reader has to assemble the meaning from
 * five separate boxes. "88.9%" on its own also does not say whether that is
 * every file at 89 or most files at 100 with an untested corner, which is a
 * different problem with a different fix, so the band split under the figure
 * is part of the figure rather than another statistic.
 */

import { useMemo } from "react";
import type { ReactNode } from "react";
import type {
  CoverageFileRow,
  CoverageHistoryPoint,
  CoverageSummary,
} from "@repowise-dev/types/health";

import { PageLede } from "../shared/page-lede";
import { Sparkline } from "./sparkline";
import { StatRibbon, type RibbonStat } from "../stats/stat-ribbon";
import { formatDate, formatDateTime, formatNumber } from "../lib/format";
import { coverageBand } from "./tokens";

export interface CoverageLedeProps {
  summary: CoverageSummary;
  /** Instrumented file rows, for the band split under the figure. */
  files: CoverageFileRow[];
  /** Directories carrying coverable lines. */
  moduleCount: number;
  /** Rendered under the prose. */
  action?: ReactNode;
  /** One point per ingested report, oldest first, for the trend under the figure. */
  history?: CoverageHistoryPoint[] | undefined;
}

const MIN_TREND_POINTS = 3;

/** Band order for the split bar: worst first, matching how the tables list. */
const BANDS = [
  { key: "thin", label: "Thin", max: 30, bar: "bg-[var(--color-error)]" },
  { key: "partial", label: "Partial", max: 60, bar: "bg-[var(--color-warning)]" },
  { key: "solid", label: "Solid", max: 80, bar: "bg-[var(--color-caution)]" },
  { key: "strong", label: "Strong", max: 101, bar: "bg-[var(--color-success)]" },
] as const;

export function CoverageLede({
  summary,
  files,
  moduleCount,
  action,
  history,
}: CoverageLedeProps) {
  const pct = summary.line_coverage_pct;
  const band = pct == null ? null : coverageBand(pct);
  const uncovered = Math.max(0, summary.total_lines - summary.covered_lines);

  // Weighted by coverable lines rather than by file count, for the same reason
  // the health distribution weights by NLOC: one large untested module must not
  // hide behind a hundred fully covered one-line shims.
  const split = useMemo(() => {
    const lines = BANDS.map(() => 0);
    let total = 0;
    for (const f of files) {
      // A file with no coverable lines has no percentage to band. Counting it
      // as 0% would invent a thin slice out of empty `__init__` files.
      if (f.total_coverable_lines <= 0 || f.line_coverage_pct == null) continue;
      const i = BANDS.findIndex((b) => f.line_coverage_pct! < b.max);
      const at = i === -1 ? BANDS.length - 1 : i;
      lines[at] = (lines[at] ?? 0) + f.total_coverable_lines;
      total += f.total_coverable_lines;
    }
    if (total === 0) return null;
    return BANDS.map((b, i) => ({
      ...b,
      pct: Math.round(((lines[i] ?? 0) / total) * 1000) / 10,
    })).filter((b) => b.pct > 0);
  }, [files]);

  // Two reports are a comparison, not a trend; the line starts at three. The
  // history leaves partial reports out, so beside a partial headline it would
  // end on a different figure than the one it sits under.
  const trend =
    !summary.mapping_partial && history && history.length >= MIN_TREND_POINTS
      ? history.map((p) => p.line_coverage_pct)
      : null;

  const stats: RibbonStat[] = [
    {
      label: "Files instrumented",
      value: formatNumber(summary.file_count),
      ...(moduleCount > 0
        ? { sub: `across ${formatNumber(moduleCount)} directories` }
        : {}),
    },
    {
      label: "Uncovered lines",
      value: formatNumber(uncovered),
      sub: `of ${formatNumber(summary.total_lines)} coverable`,
    },
    {
      label: "Branch coverage",
      value:
        summary.branch_coverage_pct == null
          ? ""
          : `${summary.branch_coverage_pct.toFixed(1)}%`,
      hint: "Share of conditional branches a test takes both ways. Many runners emit no branch data at all, in which case this cell is absent rather than zero.",
    },
    {
      label: "Report format",
      value: (summary.source_format ?? "").toUpperCase(),
      ...(summary.ingested_at
        ? { sub: `ingested ${formatDate(summary.ingested_at)}` }
        : {}),
    },
  ];

  return (
    <div className="flex flex-col gap-6">
      <PageLede
        label="Line coverage"
        value={pct == null ? "—" : `${pct.toFixed(1)}%`}
        {...(band ? { valueColor: band.color, band } : {})}
        unit="of coverable lines"
        layout="beside"
        {...(action ? { action } : {})}
        figureFooter={
          split || trend ? (
            <div className="space-y-3">
              {split ? <BandSplit split={split} /> : null}
              {trend ? <CoverageTrend values={trend} /> : null}
            </div>
          ) : undefined
        }
      >
        <p>
          Tests reach{" "}
          <strong className="font-semibold text-[var(--color-text-primary)]">
            {formatNumber(summary.covered_lines)} of {formatNumber(summary.total_lines)}{" "}
            coverable lines
          </strong>{" "}
          across {formatNumber(summary.file_count)} instrumented files
          {moduleCount > 0 ? ` in ${formatNumber(moduleCount)} directories` : ""}.
          {band ? ` We rate that ${band.label.toLowerCase()}.` : ""} These are the
          lines your own test run executed, so every figure on this tab is
          line-level and exact. Without a report we can still name which tests
          reach which files, but not how much of a file they run.
        </p>

        <p className="mt-2.5">
          <strong className="font-semibold text-[var(--color-text-primary)]">
            {formatNumber(uncovered)} lines
          </strong>{" "}
          have no test executing them. Where that matters is not evenly spread, which
          is what the map below is for: an uncovered line in a file nothing depends on
          and nobody edits costs little, and the same line in a file that changes
          weekly is where defects arrive.
        </p>

        {summary.source_format && (
          <p className="mt-2.5">
            Read from{" "}
            <span className="font-mono text-[var(--color-text-primary)]">
              {summary.source_format}
            </span>{" "}
            output
            {summary.ingested_at
              ? `, ingested ${formatDateTime(summary.ingested_at)}`
              : ""}
            {summary.ingested_commit_sha
              ? ` at ${summary.ingested_commit_sha.slice(0, 8)}`
              : ""}
            . Re-ingest after a test run to move these figures;{" "}
            {summary.branch_coverage_pct == null
              ? "this report carries no branch data, so branch coverage is absent rather than zero."
              : "branch coverage rides on the same report."}
          </p>
        )}

        <StaleNotice summary={summary} />
      </PageLede>

      <StatRibbon stats={stats} />
    </div>
  );
}

/** Current is the quiet default; only a report from another commit is marked. */
function StaleNotice({ summary }: { summary: CoverageSummary }) {
  if (summary.freshness?.status !== "stale") return null;
  return (
    <p className="mt-2.5">
      <strong className="font-semibold text-[var(--color-warning)]">
        Measured at another commit.
      </strong>{" "}
      The report ran at{" "}
      <span className="font-mono text-[var(--color-text-primary)]">
        {summary.ingested_commit_sha?.slice(0, 8)}
      </span>
      {summary.freshness.indexed_commit ? (
        <>
          {" "}and the index is at{" "}
          <span className="font-mono text-[var(--color-text-primary)]">
            {summary.freshness.indexed_commit.slice(0, 8)}
          </span>
        </>
      ) : null}
      , so its line numbers may describe code that has since moved. Run the
      tests and ingest again to bring it level.
    </p>
  );
}

/**
 * Line coverage across the retained reports, on the full 0 to 100 scale so a
 * small move reads as small. Neutral ink: a direction is not a verdict, and
 * the sentence says which way it went.
 */
function CoverageTrend({ values }: { values: number[] }) {
  // Rounded before subtracting, so the delta is the gap between the two
  // figures the sentence prints.
  const round = (v: number) => Math.round(v * 10) / 10;
  const first = round(values[0]!);
  const last = round(values[values.length - 1]!);
  const delta = round(last - first);
  const span = `across the last ${values.length} reports`;
  const sentence =
    delta !== 0
      ? `${delta > 0 ? "Up" : "Down"} ${Math.abs(delta).toFixed(1)} points ${span}, from ${first.toFixed(1)}% to ${last.toFixed(1)}%.`
      : values.every((v) => round(v) === last)
        ? `Unchanged at ${last.toFixed(1)}% ${span}.`
        : `Back at ${last.toFixed(1)}% ${span}.`;
  return (
    <div className="flex items-center gap-3 text-[var(--color-text-tertiary)]">
      <Sparkline values={values} width={96} height={24} domain={[0, 100]} />
      <p className="text-xs tabular-nums">{sentence}</p>
    </div>
  );
}

/** The one second read that belongs to the percentage itself. */
function BandSplit({
  split,
}: {
  split: { key: string; label: string; bar: string; pct: number }[];
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
        {split.map((b) => (
          <div
            key={b.key}
            className={b.bar}
            style={{ width: `${b.pct}%` }}
            aria-label={`${b.label} ${b.pct}%`}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-[var(--color-text-tertiary)]">
        {split.map((b) => (
          <span key={b.key} className="inline-flex items-center gap-1 tabular-nums">
            <span className={`inline-block h-1.5 w-1.5 rounded-full ${b.bar}`} />
            {b.pct}% {b.label.toLowerCase()}
          </span>
        ))}
      </div>
    </div>
  );
}
