import { Shield } from "lucide-react";
import { EmptyState } from "../shared/empty-state";
import { coverageColor } from "../health/tokens";
import { formatRelativeTime } from "../lib/format";
import type { FileDetailCoverage } from "@repowise-dev/types/files";

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
        icon={<Shield className="h-8 w-8" />}
        title="No coverage data"
        description="Ingest a coverage report (repowise coverage add <report>) to see line-level coverage here."
      />
    );
  }

  const pct = coverage.line_coverage_pct;
  const coveredCount = coverage.covered_lines.length;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="text-2xl font-semibold tabular-nums" style={{ color: coverageColor(pct) }}>
          {pct.toFixed(1)}%
        </span>
        <span className="text-xs text-[var(--color-text-secondary)]">
          {coveredCount.toLocaleString()} of {coverage.total_coverable_lines.toLocaleString()}{" "}
          coverable lines hit
          {coverage.branch_coverage_pct != null &&
            ` · branches ${coverage.branch_coverage_pct.toFixed(1)}%`}
        </span>
        <span className="text-[10px] text-[var(--color-text-tertiary)] ml-auto">
          {coverage.source_format}
          {coverage.ingested_at && ` · ingested ${formatRelativeTime(coverage.ingested_at)}`}
          {coverage.ingested_commit_sha && ` @ ${coverage.ingested_commit_sha.slice(0, 7)}`}
        </span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
        <div
          className="h-full rounded-full"
          style={{ width: `${pct}%`, background: coverageColor(pct) }}
        />
      </div>

      {coverageCodeHtml ? (
        <div className="coverage-code overflow-x-auto rounded-md border border-[var(--color-border-default)] text-xs leading-relaxed">
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
        <p className="text-xs text-[var(--color-text-tertiary)]">
          Source preview unavailable — coverage summary only.
        </p>
      )}
    </div>
  );
}
