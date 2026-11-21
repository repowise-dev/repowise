import type { ReactNode } from "react";
import { PageLede } from "../shared/page-lede";
import { coverageBand } from "../health/tokens";
import { COVERAGE_REPORT_FORMATS } from "../health/coverage-formats";
import { formatNumber } from "../lib/format";
import type { FileDetailCoverage } from "@repowise-dev/types/files";
import { FileSection, Fig } from "./file-section";
import { RelativeTime } from "../shared/relative-time";
import { FileSourceCode } from "./file-source-code";

interface FileCoverageTabProps {
  coverage: FileDetailCoverage | null;
  /**
   * Shiki-highlighted source HTML where each `.line` carries a
   * `data-covered="y" | "n"` attribute (host adds it via a transformer).
   * When absent we fall back to a summary-only view.
   */
  coverageCodeHtml?: string | undefined;
  /** Names the file a selection inside the source came from. */
  filePath?: string;
  /** A `?line=N` deep link: the source line to scroll to and mark. */
  highlightLine?: number | undefined;
  /**
   * The graph-inferred "which tests reach this file" block, supplied by the host
   * already wrapped. A node rather than a fetcher because the tab bodies are
   * server components; see `buildFilePanels`.
   */
  testsPanel?: ReactNode | undefined;
}

export function FileCoverageTab({
  coverage,
  coverageCodeHtml,
  filePath,
  highlightLine,
  testsPanel,
}: FileCoverageTabProps) {
  // Rendered on both bases. Without a report it is the whole answer; with one it
  // adds what a coverage report structurally cannot give at file level - the
  // names of the tests, rather than how much of the file ran.
  const reaching = testsPanel ?? null;

  if (!coverage) {
    // Not a dead end any more. No report was ingested, and the dependency graph
    // can still name the tests that reach this file, with no setup at all.
    return (
      <div className="flex flex-col gap-6">
        {reaching}
        <FileSection
          title="Line-level coverage"
          description="What a report would add here: which lines of this file your tests actually executed, rather than which tests can reach it."
        >
          <p className="max-w-[62ch] text-xs leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
            <span className="font-mono text-[var(--color-text-primary)]">
              repowise coverage setup
            </span>{" "}
            prints the test command that writes a report here and the line that adds it. Any of{" "}
            {COVERAGE_REPORT_FORMATS.join(", ")} works.
          </p>
        </FileSection>
      </div>
    );
  }

  const pct = coverage.line_coverage_pct;
  // `coverageBand().color` is a CSS custom-property reference. `coverageColor()`
  // returns a Tailwind *class* string, and this file used to put it straight
  // into `style={{ color }}` / `background:` — which is not a colour, so the
  // figure and the bar had been painting default ink the whole time.
  const band = coverageBand(pct);
  const coveredCount = coverage.covered_line_count ?? coverage.covered_lines.length;
  const uncovered = Math.max(0, coverage.total_coverable_lines - coveredCount);
  // Covered line numbers from an earlier version of the file would tint the
  // wrong lines, so a stale file shows no tinted source at all.
  const stale = coverage.freshness === "stale";
  const codeHtml = stale ? undefined : coverageCodeHtml;

  return (
    <div>
      {/* `PageLede`, not a hand-rolled copy of it. Its docstring says it was
          extracted precisely because "three hand-rolled copies is how the
          44 / 48 / 52 sizes drift apart", and a second band chip built out of
          `color-mix` by hand is the pill rule 9 kills. `CoverageLede` is the
          wrong shape here — it opens a whole *surface* and wants a
          `CoverageSummary` plus per-file rows to split. */}
      <PageLede
        label="Line coverage"
        value={`${pct.toFixed(1)}%`}
        valueColor={band.color}
        unit="of coverable lines"
        band={band}
        layout="beside"
        figureFooter={
          <div
            className="h-2 w-full overflow-hidden rounded-full bg-[var(--color-bg-inset)]"
            role="img"
            aria-label={`${pct.toFixed(1)}% of coverable lines covered`}
          >
            <div
              className="h-full rounded-full"
              style={{ width: `${Math.min(100, Math.max(0, pct))}%`, background: band.color }}
            />
          </div>
        }
      >
        <p>
          Tests ran{" "}
          <Fig>
            {formatNumber(coveredCount)} of {formatNumber(coverage.total_coverable_lines)}{" "}
            coverable lines
          </Fig>
          , leaving <Fig>{formatNumber(uncovered)}</Fig> that no test executed
          {coverage.branch_coverage_pct != null && (
            <>
              . Branch coverage is <Fig>{coverage.branch_coverage_pct.toFixed(1)}%</Fig>
            </>
          )}
          . Measured by your own test run, not inferred from the dependency graph.
        </p>
        <p className="mt-2.5">
          Read from a{" "}
          <span className="font-mono text-[var(--color-text-primary)]">
            {coverage.source_format}
          </span>{" "}
          report
          {coverage.ingested_at && <> ingested <RelativeTime date={coverage.ingested_at} /></>}
          {coverage.ingested_commit_sha && (
            <>
              {" "}
              at{" "}
              <span className="font-mono text-[var(--color-text-primary)]">
                {coverage.ingested_commit_sha.slice(0, 7)}
              </span>
            </>
          )}
          . Re-ingest after a test run to move these figures.
        </p>
      </PageLede>

      {reaching ? <div className="mt-6">{reaching}</div> : null}

      <FileSection
        title="Source"
        description={
          codeHtml
            ? coveredCount > 0
              ? "Every coverable line, tinted by whether the last report saw it run."
              : "The last report saw no line of this file run."
            : stale
              ? "This file changed after coverage was measured; re-run tests with coverage to refresh."
              : "The highlighted source appears here for files under the render cap once a coverage report has been ingested for them."
        }
      >
        {codeHtml ? (
          <FileSourceCode html={codeHtml} filePath={filePath} highlightLine={highlightLine} />
        ) : (
          <p className="text-sm text-[var(--color-text-tertiary)]">
            {stale
              ? "Source preview hidden until coverage is measured again."
              : "Source preview unavailable for this file."}
          </p>
        )}
      </FileSection>
    </div>
  );
}
