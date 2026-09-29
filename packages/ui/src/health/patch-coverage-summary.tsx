/**
 * Patch coverage of one change: how many of the lines it changed that can
 * execute the stored test coverage actually ran, and where the rest are.
 *
 * A verdict sentence, not a figure with a band. There is no threshold here to
 * colour against, and a gate belongs to CI (`repowise coverage check`), so the
 * only marked state is stale evidence: coverage measured at another commit
 * describes other code, and the line numbers below it may not line up.
 */

import type {
  PatchCoverageFile,
  PatchCoverageResponse,
} from "@repowise-dev/types/generated/http";

export interface PatchCoverageSummaryProps {
  coverage: PatchCoverageResponse;
  /** Makes each listed path open its file. */
  onOpenFile?: (path: string) => void;
}

/** Files with uncovered lines listed before the rest collapse into a count. */
const MAX_FILES = 10;

/** Floors to one decimal so 99.99 never reads as 100.0. The epsilon absorbs
 *  binary error (57.3 * 10 is 572.99...) that would otherwise floor a step low. */
export function floorPct(pct: number): string {
  return (Math.floor(pct * 10 + 1e-9) / 10).toFixed(1);
}

/** `[[3, 3], [7, 9]]` as `3, 7-9`. */
export function formatLineRanges(ranges: number[][]): string {
  return ranges
    .map(([start, end]) =>
      end == null || end === start ? `${start}` : `${start}-${end}`,
    )
    .join(", ");
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

export function PatchCoverageSummary({
  coverage,
  onOpenFile,
}: PatchCoverageSummaryProps) {
  const pct = coverage.patch_coverage_pct;
  const uncovered = coverage.files.filter(
    (f) => f.status === "measured" && f.uncovered_ranges.length > 0,
  );
  const unmeasured = coverage.files.filter(
    (f) => f.status === "not_in_report" || f.status === "no_line_data",
  );
  const measuredAt = coverage.scope.measured_commit?.slice(0, 7) ?? null;

  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-[15px] text-[var(--color-text-secondary)]">
        {pct == null ? (
          // Unknown is a sentence, never 0%: nothing the report measured changed.
          <>
            <span className="font-semibold text-[var(--color-text-primary)]">
              Patch coverage not measured
            </span>{" "}
            · no changed line is an executable line the coverage report measured
          </>
        ) : (
          <>
            <span className="font-semibold text-[var(--color-text-primary)]">
              Patch coverage{" "}
              <span className="tabular-nums">{floorPct(pct)}%</span>
            </span>{" "}
            ·{" "}
            <span className="tabular-nums">
              {coverage.covered_line_count} of{" "}
              {plural(coverage.coverable_line_count, "changed executable line")}{" "}
              covered
            </span>
          </>
        )}
      </p>

      {coverage.scope.freshness === "stale" && (
        <p className="flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
          <span
            aria-hidden
            className="inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-warning)]"
          />
          <span>
            <span className="sr-only">Stale: </span>
            Coverage was measured at{" "}
            {measuredAt ? (
              <code className="font-mono">{measuredAt}</code>
            ) : (
              "another commit"
            )}
            , not at this change&apos;s head
          </span>
        </p>
      )}

      {coverage.scope.mapping_partial && (
        <p className="flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
          <span
            aria-hidden
            className="inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-warning)]"
          />
          <span>
            Most report paths did not match this repository, so this covers a fragment
          </span>
        </p>
      )}

      {coverage.scope.ignored_file_count > 0 && (
        <p className="text-xs text-[var(--color-text-tertiary)]">
          {plural(coverage.scope.ignored_file_count, "changed file")} ignored by{" "}
          <code className="font-mono">coverage.ignore</code>
        </p>
      )}

      {uncovered.length > 0 && (
        <ul className="mt-1 flex flex-col gap-0.5">
          {uncovered.slice(0, MAX_FILES).map((f) => (
            <FileRow
              key={f.file_path}
              file={f}
              trailing={`lines ${formatLineRanges(f.uncovered_ranges)}`}
              onOpenFile={onOpenFile}
            />
          ))}
          {uncovered.length > MAX_FILES && (
            <li className="px-1.5 pt-1 text-xs text-[var(--color-text-tertiary)]">
              and {uncovered.length - MAX_FILES} more
            </li>
          )}
        </ul>
      )}

      {unmeasured.length > 0 && (
        <details className="group mt-1">
          <summary className="cursor-pointer text-xs text-[var(--color-text-tertiary)]">
            {plural(unmeasured.length, "changed file")} the coverage report does
            not measure
          </summary>
          <ul className="mt-1 flex flex-col gap-0.5">
            {unmeasured.map((f) => (
              <FileRow
                key={f.file_path}
                file={f}
                trailing={
                  f.status === "not_in_report" ? "not in report" : "no line data"
                }
                onOpenFile={onOpenFile}
              />
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function FileRow({
  file,
  trailing,
  onOpenFile,
}: {
  file: PatchCoverageFile;
  trailing: string;
  onOpenFile: ((path: string) => void) | undefined;
}) {
  const body = (
    <>
      <span className="min-w-0 flex-1 truncate font-mono text-xs text-[var(--color-text-primary)]">
        {file.file_path}
      </span>
      <span className="shrink-0 font-mono text-xs tabular-nums text-[var(--color-text-tertiary)]">
        {trailing}
      </span>
    </>
  );
  const row = "flex w-full items-center gap-2 rounded px-1.5 py-1 text-left";
  return (
    <li>
      {onOpenFile ? (
        <button
          type="button"
          onClick={() => onOpenFile(file.file_path)}
          title={`Open ${file.file_path}`}
          className={`${row} transition-colors hover:bg-[var(--color-bg-surface)]`}
        >
          {body}
        </button>
      ) : (
        <div className={row} title={file.file_path}>
          {body}
        </div>
      )}
    </li>
  );
}
