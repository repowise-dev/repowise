import { Shield } from "lucide-react";
import { EmptyState } from "../shared/empty-state";
import { PageLede } from "../shared/page-lede";
import { coverageBand } from "../health/tokens";
import { formatNumber, formatRelativeTime } from "../lib/format";
import type { FileDetailCoverage } from "@repowise-dev/types/files";
import { FileSection, Fig } from "./file-section";

interface FileCoverageTabProps {
  coverage: FileDetailCoverage | null;
  /**
   * Shiki-highlighted source HTML where each `.line` carries a
   * `data-covered="y" | "n"` attribute (host adds it via a transformer).
   * When absent we fall back to a summary-only view.
   */
  coverageCodeHtml?: string | undefined;
}

export function FileCoverageTab({ coverage, coverageCodeHtml }: FileCoverageTabProps) {
  if (!coverage) {
    return (
      <EmptyState
        titleAs="h2"
        icon={<Shield className="h-8 w-8" />}
        title="No coverage report ingested"
        description="Run `repowise coverage add <report>` with an lcov, cobertura or coverage.py file and line-level coverage appears here."
      />
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
          Tests reach{" "}
          <Fig>
            {formatNumber(coveredCount)} of {formatNumber(coverage.total_coverable_lines)}{" "}
            coverable lines
          </Fig>
          , leaving <Fig>{formatNumber(uncovered)}</Fig> with nothing executing them
          {coverage.branch_coverage_pct != null && (
            <>
              . Branch coverage is <Fig>{coverage.branch_coverage_pct.toFixed(1)}%</Fig>
            </>
          )}
          . This is read from your own test run, not inferred.
        </p>
        <p className="mt-2.5">
          Read from a{" "}
          <span className="font-mono text-[var(--color-text-primary)]">
            {coverage.source_format}
          </span>{" "}
          report
          {coverage.ingested_at && <> ingested {formatRelativeTime(coverage.ingested_at)}</>}
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

      <FileSection
        title="Source"
        description={
          coverageCodeHtml
            ? "Every coverable line, tinted by whether the last report saw it run."
            : "The highlighted source appears here for files under the render cap once a coverage report has been ingested for them."
        }
      >
        {coverageCodeHtml ? (
          <div className="coverage-code overflow-x-auto border-y border-[var(--color-border-default)] text-xs leading-relaxed">
            {/* Highlighted by the host through the shared shiki path; the
                data-covered line attributes drive the gutter tint below. */}
            <div dangerouslySetInnerHTML={{ __html: coverageCodeHtml }} />
            <style>{`
            .coverage-code pre { margin: 0; padding: 0.75rem 0; background: transparent !important; }
            .coverage-code code { display: block; }
            .coverage-code .line { display: inline-block; width: 100%; padding: 0 0.75rem 0 0.5rem; border-left: 3px solid transparent; }
            .coverage-code .line[data-covered="y"] { border-left-color: var(--color-success); background: color-mix(in srgb, var(--color-success) 7%, transparent); }
            .coverage-code .line[data-covered="n"] { border-left-color: var(--color-error); background: color-mix(in srgb, var(--color-error) 8%, transparent); }
          `}</style>
          </div>
        ) : (
          <p className="text-sm text-[var(--color-text-tertiary)]">
            Source preview unavailable for this file.
          </p>
        )}
      </FileSection>
    </div>
  );
}
