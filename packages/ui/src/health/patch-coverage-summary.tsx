/**
 * Patch coverage of one change: how many of the lines it changed that can
 * execute the stored test coverage actually ran, and where the rest are.
 *
 * A verdict sentence, not a figure with a band. There is no threshold here to
 * colour against, and a gate belongs to CI (`repowise coverage check`), so the
 * only marked state is stale evidence: coverage measured at another commit
 * describes other code, and the line numbers below it may not line up. The
 * exception is path-scoped gates (`coverage.gates`), which carry their own
 * thresholds: each is a dot plus a word.
 */

import type {
  PatchCoverageFile,
  PatchCoveragePathGate,
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

function plural(n: number, word: string, many?: string): string {
  return `${n} ${n === 1 ? word : (many ?? `${word}s`)}`;
}

/** A path-scoped gate's verdict in words, as `coverage check` prints it. */
const GATE_TEXT: Record<PatchCoveragePathGate["gate"], string> = {
  pass: "passes",
  fail: "fails",
  no_data: "no measured changed lines",
  not_set: "no threshold",
  too_small: "too few changed lines to judge",
};

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
  // Guarded: a server older than path-scoped gates sends neither field.
  const pathGates = coverage.path_gates ?? [];
  const configErrors = coverage.scope.config_errors ?? [];

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

      {configErrors.length > 0 && (
        <p className="flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
          <span
            aria-hidden
            className="inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-warning)]"
          />
          <span>
            {plural(configErrors.length, "invalid entry", "invalid entries")} in{" "}
            <code className="font-mono">coverage.gates</code>, so no path-scoped
            gate is judged
          </span>
        </p>
      )}

      {pathGates.length > 0 && (
        <div className="mt-1 flex flex-col gap-0.5">
          {/* The list carries the same name, so the visible label is not read twice. */}
          <p aria-hidden className="px-1.5 text-xs text-[var(--color-text-tertiary)]">
            Path-scoped gates
          </p>
          <ul aria-label="Path-scoped gates" className="flex flex-col gap-0.5">
            {pathGates.map((g) => (
              <PathGateRow key={g.name} gate={g} />
            ))}
          </ul>
        </div>
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

function PathGateRow({ gate }: { gate: PatchCoveragePathGate }) {
  const failed = gate.gate === "fail";
  // Red only for a gate that fails the change; an informational miss is amber.
  const dot = failed
    ? gate.informational
      ? "bg-[var(--color-warning)]"
      : "bg-[var(--color-error)]"
    : gate.gate === "pass"
      ? "bg-[var(--color-success)]"
      : "bg-[var(--color-text-tertiary)]";
  const unmeasured = gate.unmeasured_file_count ?? 0;
  let verdict =
    failed && gate.informational ? "below threshold" : GATE_TEXT[gate.gate];
  if (gate.gate === "no_data" && gate.coverable_line_count > 0) {
    // Counted but not judged: stale coverage or invalid config, said above.
    verdict = "not judged";
  } else if (gate.gate === "no_data" && unmeasured > 0) {
    verdict += ` (${plural(unmeasured, "changed file")} not measured)`;
  }
  const pct = gate.patch_coverage_pct;
  return (
    <li
      className="flex items-center gap-2 px-1.5 py-0.5 text-xs"
      title={gate.paths.join(", ")}
    >
      <span aria-hidden className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
      <span className="min-w-0 flex-1 truncate font-mono text-[var(--color-text-primary)]">
        {gate.name}
      </span>
      <span className="shrink-0 text-[var(--color-text-secondary)]">
        {verdict}
        {gate.informational ? " (informational)" : ""}
      </span>
      <span className="shrink-0 font-mono tabular-nums text-[var(--color-text-tertiary)]">
        {pct == null
          ? "n/a"
          : `${gate.covered_line_count} of ${gate.coverable_line_count} (${floorPct(pct)}%)`}
        {gate.threshold == null ? "" : ` · gate ${floorPct(gate.threshold)}%`}
      </span>
    </li>
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
