"use client";

/**
 * The Tests tab on the inferred basis: what the dependency graph knows about
 * which files a test reaches, when no coverage report was ever ingested.
 *
 * This replaces a dead end. The tab used to answer "no coverage report" with
 * setup instructions and the line "Nothing is inferred: we read the lines your
 * tests really executed", which stopped being true the moment the graph could
 * answer the question, and read as the product apologising for something it had
 * just fixed.
 *
 * Three rules run through every figure here, all inherited from the core and
 * none of them negotiable:
 *
 * **No percentage, and no bar.** Reaching is a file-level fact with no line
 * attribution behind it, so a ratio built from it would be a coverage figure the
 * data cannot support. Counts only. That is also why the hero is a small
 * integer rather than "1,074 of 2,185 files reached", which begs for a bar and a
 * bar is a percentage.
 *
 * **No health-band colour on inferred data.** Green, amber and red are reserved
 * for health readouts where they carry a band. Painting "nothing reaches this"
 * red would make a static reading look like a measurement.
 *
 * **A relationship, not a score.** The strongest thing this tab can say is
 * "these six tests run this file", and it says that. The moment it becomes a
 * number on a scale we have rebuilt the confusion both core changes spent all
 * their care avoiding.
 */

import { useMemo, useState } from "react";
import type {
  HealthCoverageResponse,
  HealthFinding,
  InferredTestMap,
  ReachedFileRow,
} from "@repowise-dev/types/health";

import { OverviewSection } from "../overview/section";
import { PageLede } from "../shared/page-lede";
import { ResponsiveTable, type ResponsiveColumn } from "../shared/responsive-table";
import { ResultsFooter } from "../shared/results-footer";
import { formatNumber } from "../lib/format";

import { RiskCoverageScatter } from "./risk-coverage-scatter";
import { scoreBadgeClass } from "./tokens";
import { COVERAGE_REPORT_FORMATS_LABEL } from "./coverage-formats";
import { CiHint } from "../shared/ci-hint";

/** Below this, a file no test reaches is worth leading with. */
const AT_RISK_SCORE = 6;

/** The floor statement the hero and the list share. */
export const UNREACHED_TITLE = "No test reaches these through the graph";

/** A file no test reaches that owes one: generated, vendored, build, tooling,
 * example and migration code never does. */
function isGap(f: ReachedFileRow): boolean {
  return !f.reached && !f.not_applicable;
}

/** Says how many files owe no test, so the two counts above read as the whole. */
function NotApplicableNote({ count }: { count: number }) {
  if (count === 0) return null;
  const one = count === 1;
  return (
    <>
      {" "}
      {formatNumber(count)} {one ? "is" : "are"} generated, vendored, build, tooling,
      example or migration code, which owes no test, so {one ? "it is" : "they are"} not
      counted as unreached.
    </>
  );
}

export function InferredTestsView({
  data,
  untestedFindings,
  onOpenFile,
  unreachedTitle = UNREACHED_TITLE,
}: {
  data: HealthCoverageResponse;
  untestedFindings: HealthFinding[];
  onOpenFile: (path: string) => void;
  /** Localised {@link UNREACHED_TITLE}. */
  unreachedTitle?: string | undefined;
}) {
  const map = data.inferred as InferredTestMap;
  const PAGE = 25;
  const [visible, setVisible] = useState(PAGE);

  const points = useMemo(
    () =>
      map.files
        .filter((f) => f.health_score != null && !f.not_applicable)
        .map((f) => ({
          file_path: f.file_path,
          health_score: f.health_score!,
          line_coverage_pct: null,
          nloc: f.nloc ?? 0,
          reached: f.reached,
        })),
    [map.files],
  );

  // The hero. Scoped to what is actionable rather than to the whole repo: a
  // small integer cannot be misread as coverage. Counted from the same rows as
  // the list below, so the two can never describe different sets.
  const atRisk = useMemo(
    () =>
      map.files.filter(
        (f) => isGap(f) && f.health_score != null && f.health_score < AT_RISK_SCORE,
      ).length,
    [map.files],
  );

  // The untested-hotspot findings only order the list (churn and dependents
  // make a gap there cost more); they never add or remove a row.
  const ranked = useMemo(() => {
    const rank = new Map(untestedFindings.map((f, i) => [f.file_path, i]));
    return map.files
      .filter(isGap)
      .sort((a, b) => {
        const ra = rank.get(a.file_path);
        const rb = rank.get(b.file_path);
        if (ra != null && rb != null) return ra - rb;
        if (ra != null) return -1;
        if (rb != null) return 1;
        return (a.health_score ?? 10) - (b.health_score ?? 10);
      });
  }, [map.files, untestedFindings]);

  const trimmed = map.files_total > map.files.length;

  const columns: ResponsiveColumn<(typeof ranked)[number]>[] = [
    {
      key: "file_path",
      header: "File",
      priority: 1,
      render: (f) => (
        <span
          className="block truncate font-mono text-xs text-[var(--color-text-primary)]"
          title={f.file_path}
        >
          {f.file_path}
        </span>
      ),
    },
    {
      key: "nloc",
      header: "Lines",
      priority: 3,
      align: "right",
      render: (f) => (
        <span className="tabular-nums text-[var(--color-text-tertiary)]">
          {f.nloc ?? "—"}
        </span>
      ),
    },
    {
      key: "health_score",
      header: "Code health",
      priority: 2,
      align: "right",
      render: (f) =>
        f.health_score == null ? (
          <span className="text-[var(--color-text-tertiary)]">—</span>
        ) : (
          <span
            className={`inline-block rounded px-1.5 py-0.5 text-xs font-semibold ${scoreBadgeClass(f.health_score)}`}
          >
            {f.health_score.toFixed(1)}
          </span>
        ),
    },
  ];

  return (
    <div className="flex flex-col gap-6 sm:gap-8">
      <PageLede
        label={unreachedTitle}
        value={formatNumber(atRisk)}
        unit={atRisk === 1 ? "weak file" : "weak files"}
        layout="beside"
      >
        <p>
          No coverage report has been ingested, so this reads the dependency
          graph instead: a file is <strong className="font-semibold text-[var(--color-text-primary)]">reached</strong>{" "}
          when a test calls into it or imports it, directly or through a chain
          of imports. Your{" "}
          {formatNumber(map.test_file_count)} test files reach{" "}
          <strong className="font-semibold text-[var(--color-text-primary)]">
            {formatNumber(map.files_reached)} of {formatNumber(map.files_total)} files
          </strong>
          . The graph finds no test for the other{" "}
          {formatNumber(map.files_not_reached)}.
          <NotApplicableNote count={map.files_not_applicable ?? 0} />
        </p>

        <p className="mt-2.5">
          The figure beside this counts those files that also score as weak.
          Treat the list as a floor: it misses code a framework invokes rather
          than your code calling it, such as route handlers, CLI commands and
          plugin registries. Reaching is not executing either, so &ldquo;a test
          reaches this&rdquo; is safe to trust and &ldquo;this much is
          tested&rdquo; is something it cannot tell you.
        </p>
      </PageLede>

      <OverviewSection
        title="Health against tests"
        description="Every file placed by its 0–10 defect-health score (higher is healthier), split by whether any test reaches it, sized by lines of code. There is no percentage axis here because there is no line-level evidence to build one from. The left column is the page: weak code no test reaches through the graph. Click a file to open it."
      >
        <RiskCoverageScatter
          basis="inferred"
          points={points}
          onSelect={(p) => onOpenFile(p.file_path)}
        />
      </OverviewSection>

      <OverviewSection
        title={unreachedTitle}
        description="Worst health first, and the files the untested-hotspot marker flagged lead the list because churn and dependents make a gap there cost more. Code a framework invokes (routes, CLI commands, plugin registries) can land here with tests behind it, so open a file to check before writing tests for it."
      >
        <div className="border-t border-[var(--color-border-default)]">
          <ResponsiveTable
            columns={columns}
            rows={ranked.slice(0, visible)}
            rowKey={(f) => f.file_path}
            onRowClick={(f) => onOpenFile(f.file_path)}
            stacked="sm"
            bare
          />
          {ranked.length > 0 ? (
            <ResultsFooter
              shown={Math.min(visible, ranked.length)}
              total={ranked.length}
              hasMore={visible < ranked.length}
              onLoadMore={() => setVisible((v) => v + PAGE)}
              noun="files"
            />
          ) : null}
        </div>
        {trimmed ? (
          <p className="text-xs text-[var(--color-text-tertiary)]">
            Ranked over {formatNumber(map.files.length)} of{" "}
            {formatNumber(map.files_total)} files, capped so one tab does not pull
            the whole repository. The counts above are the repository&apos;s.
          </p>
        ) : null}
      </OverviewSection>

      <AddResolutionSection />
    </div>
  );
}

/**
 * The report as added resolution, not as the price of entry.
 *
 * The old copy sat under "No coverage report ingested yet" and read as a wall.
 * It is the same two commands; what changed is that the reader now arrives here
 * having already been answered, so the argument is what a report buys rather
 * than what its absence costs. The X axis spreading from two positions into a
 * continuum is the most honest one-line version of that.
 */
function AddResolutionSection() {
  return (
    <OverviewSection
      title="Sharpen this with a coverage report"
      description="Everything above is read from the dependency graph and needs no setup. A report adds what the graph structurally cannot see: which lines actually ran."
    >
      <div className="flex max-w-[62ch] flex-col gap-3 border-t border-[var(--color-border-default)] pt-4">
        <p className="text-xs leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
          Hand us a report and the two columns above become a continuous axis,
          every file moves to its own coverage figure, and the untested-hotspot
          list narrows from &ldquo;no test reaches this&rdquo; to the lines no test
          executed. Reaching becomes measuring.
        </p>
        <pre className="w-fit overflow-x-auto rounded-md bg-[var(--color-bg-inset)] px-3 py-2 font-mono text-xs text-[var(--color-text-primary)]">
          pytest --cov --cov-report=lcov{"\n"}
          repowise coverage add coverage.lcov
        </pre>
        <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
          {COVERAGE_REPORT_FORMATS_LABEL}
        </p>
        <CiHint command="repowise coverage check" checks="the lines each change touched" />
      </div>
    </OverviewSection>
  );
}
