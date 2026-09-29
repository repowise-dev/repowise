/**
 * Patch coverage of one change: how many of the lines it changed that can
 * execute the stored test coverage actually ran, and where the rest are.
 *
 * A verdict sentence, not a figure with a band. There is no threshold here to
 * colour against, and a gate belongs to CI (`repowise coverage check`), so the
 * marked states are few: stale or fragmentary evidence (coverage measured at
 * another commit describes other code, and the line numbers below it may not
 * line up), a risky file, whose risk words carry the warning colour, and
 * path-scoped gates (`coverage.gates`), which carry their own thresholds:
 * each is a dot plus a word.
 *
 * Each file row names its file's risk in words, and the rows read riskiest
 * first, in the order `repowise coverage check` uses. A line says where the
 * risk came from when not every file had index data. With an index, a file
 * with uncovered lines also names the test file to extend.
 *
 * When coverage was also stored at the change's base commit, one line under
 * the headline compares project coverage there with the head, or says in muted
 * text why the two measurements cannot be compared.
 */

import type { ReactNode } from "react";
import type {
  PatchCoverageFile,
  PatchCoverageFileRisk,
  PatchCoveragePathGate,
  PatchCoverageProject,
  PatchCoverageResponse,
  PatchCoverageTestHint,
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

/** A file's risk in words, as the CLI's Risk column reads; `null` when not assessed. */
export function riskWords(
  risk: PatchCoverageFileRisk | null | undefined,
): string | null {
  if (risk == null) return null;
  if (risk.basis === "unavailable") return "unknown";
  const parts = [...risk.reasons];
  if (risk.fix_pressure) parts.push(`bug-fix weight ${risk.fix_pressure.toFixed(1)}`);
  if (risk.dependents) parts.push(plural(risk.dependents, "dependent"));
  return parts.length ? parts.join(", ") : "none known";
}

/** Where the risk came from, when not every row had index data; the CLI's basis line. */
export function riskBasisLine(files: PatchCoverageFile[]): string | null {
  const bases = files.flatMap((f) => (f.risk ? [f.risk.basis] : []));
  const count = (basis: string) => bases.filter((b) => b === basis).length;
  const unknown = count("unavailable");
  const parts = [
    gitOnlyText(count("git"), bases.length),
    bases.includes("index") ? "git fix history could not be read" : "",
    unknown ? `risk could not be read for ${plural(unknown, "file")}` : "",
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
}

function gitOnlyText(gitOnly: number, total: number): string {
  if (!gitOnly) return "";
  if (gitOnly === total) {
    return "Risk is from git bug-fix history alone; an index adds hotspot, bug-magnet and dependent counts";
  }
  return `Risk for ${gitOnly} of ${plural(total, "file")} is from git bug-fix history alone: the index has no row for them yet`;
}

/** The file's first hint that names a test, else its first hint; `null` without an index. */
export function firstHint(file: PatchCoverageFile): PatchCoverageTestHint | null {
  const hints = file.hints ?? [];
  return hints.find((h) => h.tests.length > 0) ?? hints[0] ?? null;
}

/**
 * Why the hint's test is the one to extend, measured or inferred. The source
 * of truth is the core's `hint_phrase` (patch_coverage/hints.py); keep the two
 * worded alike.
 */
export function hintReason(hint: PatchCoverageTestHint): string {
  switch (hint.basis) {
    case "per_test":
      return hint.symbol
        ? `measured: runs other lines of ${hint.symbol}`
        : "measured: runs nearby lines";
    case "call_graph":
      return `inferred: calls reach ${hint.symbol ?? "this code"}`;
    case "import_graph":
      return "inferred: imports this file";
    case "none":
      return "no test reaches this; add one";
    default: {
      const unknown: never = hint.basis;
      return unknown;
    }
  }
}

/** The whole hint as one sentence, the core's `hint_phrase` without code spans. */
export function hintText(hint: PatchCoverageTestHint): string {
  const test = hint.tests[0];
  return test ? `extend ${test} (${hintReason(hint)})` : hintReason(hint);
}

/** Risky first, then fix pressure, dependents, uncovered lines: the core's order. */
export function byRisk(a: PatchCoverageFile, b: PatchCoverageFile): number {
  const key = (f: PatchCoverageFile): number[] => [
    f.risk?.risky ? 0 : 1,
    -(f.risk?.fix_pressure ?? 0),
    -(f.risk?.dependents ?? 0),
    -(f.coverable_line_count - f.covered_line_count),
  ];
  const ka = key(a);
  const kb = key(b);
  for (let i = 0; i < ka.length; i++) {
    const diff = (ka[i] ?? 0) - (kb[i] ?? 0);
    if (diff !== 0) return diff;
  }
  return a.file_path < b.file_path ? -1 : a.file_path > b.file_path ? 1 : 0;
}

type Tone = "error" | "warning" | "success" | "neutral";

const DOT: Record<Tone, string> = {
  error: "bg-[var(--color-error)]",
  warning: "bg-[var(--color-warning)]",
  success: "bg-[var(--color-success)]",
  neutral: "bg-[var(--color-text-tertiary)]",
};

/** A path-scoped gate's verdict in words, as `coverage check` prints it. Red
 *  is kept for a gate that fails the change. */
const GATE_STATUS: Record<PatchCoveragePathGate["gate"], { text: string; tone: Tone }> = {
  pass: { text: "passes", tone: "success" },
  fail: { text: "fails", tone: "error" },
  no_data: { text: "no measured changed lines", tone: "neutral" },
  not_set: { text: "no threshold", tone: "neutral" },
  too_small: { text: "too few changed lines to judge", tone: "neutral" },
};

type OpenFile = ((path: string) => void) | undefined;

export function PatchCoverageSummary({
  coverage,
  onOpenFile,
}: PatchCoverageSummaryProps) {
  return (
    <div className="flex flex-col gap-1.5">
      <Headline coverage={coverage} />
      {/* Guarded: a server older than the project delta sends no field. */}
      <ProjectLine project={coverage.project ?? null} />
      <RiskyLine risky={coverage.risky} />
      <ScopeNotes coverage={coverage} />
      {/* Guarded: a server older than path-scoped gates sends neither field. */}
      <PathGates
        gates={coverage.path_gates ?? []}
        configErrors={coverage.scope.config_errors ?? []}
      />
      <UncoveredList files={uncoveredFiles(coverage.files)} onOpenFile={onOpenFile} />
      <UnmeasuredList files={unmeasuredFiles(coverage.files)} onOpenFile={onOpenFile} />
    </div>
  );
}

/** Measured files with uncovered changed lines, riskiest first. */
function uncoveredFiles(files: PatchCoverageFile[]): PatchCoverageFile[] {
  return files
    .filter((f) => f.status === "measured" && f.uncovered_ranges.length > 0)
    .sort(byRisk);
}

/** Not-in-report files before those without line data, then by risk: the core's order. */
function unmeasuredFiles(files: PatchCoverageFile[]): PatchCoverageFile[] {
  const rank = (f: PatchCoverageFile) => Number(f.status !== "not_in_report");
  return files
    .filter((f) => f.status === "not_in_report" || f.status === "no_line_data")
    .sort((a, b) => rank(a) - rank(b) || byRisk(a, b));
}

function Headline({ coverage }: { coverage: PatchCoverageResponse }) {
  const pct = coverage.patch_coverage_pct;
  return (
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
            Patch coverage <span className="tabular-nums">{floorPct(pct)}%</span>
          </span>{" "}
          ·{" "}
          <span className="tabular-nums">
            {coverage.covered_line_count} of{" "}
            {plural(coverage.coverable_line_count, "changed executable line")} covered
          </span>
        </>
      )}
    </p>
  );
}

/**
 * Project coverage at the head against the base, worded as the core's
 * `project_line` (patch_coverage/render.py) words it: `Project coverage 81.2% ·
 * down 0.30 points from 81.5% at a1b2c3d`, then the max-drop verdict when one
 * was judged. `null` without both figures.
 */
export function projectText(project: PatchCoverageProject): string | null {
  const head = project.head?.coverage_pct;
  const base = project.base?.coverage_pct;
  if (head == null || base == null || project.delta_pct == null) return null;
  const parts = [
    `Project coverage ${floorPct(head)}%`,
    deltaPhrase(project.delta_pct, base, project.base_commit),
    gatePhrase(project),
  ];
  return parts.filter(Boolean).join(" · ");
}

/** `down 0.30 points from 81.5% at a1b2c3d`; `unchanged` at zero. */
function deltaPhrase(delta: number, base: number, baseCommit: string | null): string {
  const change =
    delta === 0 ? "unchanged" : `${delta > 0 ? "up" : "down"} ${Math.abs(delta).toFixed(2)} points`;
  const at = baseCommit ? ` at ${baseCommit.slice(0, 7)}` : "";
  return `${change} from ${floorPct(base)}%${at}`;
}

/** The max-drop verdict, when one was judged; `""` otherwise. */
function gatePhrase(project: PatchCoverageProject): string {
  if (project.gate === "fail") {
    return `falls more than the ${project.max_drop}-point max-drop gate allows`;
  }
  if (project.gate === "pass") return `within the ${project.max_drop}-point max-drop gate`;
  return "";
}

function ProjectLine({ project }: { project: PatchCoverageProject | null }) {
  if (project == null) return null;
  if (project.incomparable.length > 0) {
    return (
      <p className="text-xs text-[var(--color-text-tertiary)]">
        Project coverage not compared: {project.incomparable.join("; ")}
      </p>
    );
  }
  const text = projectText(project);
  if (text == null) return null;
  return <p className="text-xs tabular-nums text-[var(--color-text-secondary)]">{text}</p>;
}

/** Only when a risky file has changed executable lines: nothing else to report. */
function RiskyLine({ risky }: { risky: PatchCoverageResponse["risky"] | undefined }) {
  if (risky == null || risky.patch_coverage_pct == null) return null;
  return (
    <p className="text-xs tabular-nums text-[var(--color-text-secondary)]">
      Risky files {floorPct(risky.patch_coverage_pct)}% · {risky.covered_line_count}{" "}
      of {plural(risky.coverable_line_count, "changed executable line")} covered
      in {plural(risky.file_count, "risky file")}
    </p>
  );
}

/** What to read the figures with: stale or partial evidence, ignored files, risk basis. */
function ScopeNotes({ coverage }: { coverage: PatchCoverageResponse }) {
  const { scope } = coverage;
  const basis = riskBasisLine(coverage.files);
  return (
    <>
      {scope.freshness === "stale" && (
        <StaleNote measuredAt={scope.measured_commit?.slice(0, 7) ?? null} />
      )}
      {scope.mapping_partial && (
        <WarningNote>
          Most report paths did not match this repository, so this covers a fragment
        </WarningNote>
      )}
      {scope.ignored_file_count > 0 && (
        <p className="text-xs text-[var(--color-text-tertiary)]">
          {plural(scope.ignored_file_count, "changed file")} ignored by{" "}
          <code className="font-mono">coverage.ignore</code>
        </p>
      )}
      {basis != null && (
        <p className="text-xs text-[var(--color-text-tertiary)]">{basis}.</p>
      )}
    </>
  );
}

/** An amber dot plus a sentence: evidence to read with care. */
function WarningNote({ children }: { children: ReactNode }) {
  return (
    <p className="flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
      <span aria-hidden className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${DOT.warning}`} />
      <span>{children}</span>
    </p>
  );
}

function StaleNote({ measuredAt }: { measuredAt: string | null }) {
  return (
    <WarningNote>
      <span className="sr-only">Stale: </span>
      Coverage was measured at{" "}
      {measuredAt ? <code className="font-mono">{measuredAt}</code> : "another commit"}
      , not at this change&apos;s head
    </WarningNote>
  );
}

/** Path-scoped gates (`coverage.gates`): why none is judged, then each one. */
function PathGates({
  gates,
  configErrors,
}: {
  gates: PatchCoveragePathGate[];
  configErrors: string[];
}) {
  return (
    <>
      {configErrors.length > 0 && (
        <WarningNote>
          {plural(configErrors.length, "invalid entry", "invalid entries")} in{" "}
          <code className="font-mono">coverage.gates</code>, so no path-scoped gate is
          judged
        </WarningNote>
      )}
      {gates.length > 0 && (
        <div className="mt-1 flex flex-col gap-0.5">
          {/* The list carries the same name, so the visible label is not read twice. */}
          <p aria-hidden className="px-1.5 text-xs text-[var(--color-text-tertiary)]">
            Path-scoped gates
          </p>
          <ul aria-label="Path-scoped gates" className="flex flex-col gap-0.5">
            {gates.map((g) => (
              <PathGateRow key={g.name} gate={g} />
            ))}
          </ul>
        </div>
      )}
    </>
  );
}

function gateStatus(gate: PatchCoveragePathGate): { text: string; tone: Tone } {
  if (gate.gate === "fail" && gate.informational) {
    return { text: "below threshold", tone: "warning" };
  }
  if (gate.gate !== "no_data") return GATE_STATUS[gate.gate];
  // Counted but not judged: stale coverage or invalid config, said above.
  if (gate.coverable_line_count > 0) return { text: "not judged", tone: "neutral" };
  const unmeasured = gate.unmeasured_file_count ?? 0;
  const text = GATE_STATUS.no_data.text;
  return unmeasured > 0
    ? { text: `${text} (${plural(unmeasured, "changed file")} not measured)`, tone: "neutral" }
    : GATE_STATUS.no_data;
}

function gateFigures(gate: PatchCoveragePathGate): string {
  const pct = gate.patch_coverage_pct;
  const covered =
    pct == null
      ? "n/a"
      : `${gate.covered_line_count} of ${gate.coverable_line_count} (${floorPct(pct)}%)`;
  return gate.threshold == null ? covered : `${covered} · gate ${floorPct(gate.threshold)}%`;
}

function PathGateRow({ gate }: { gate: PatchCoveragePathGate }) {
  const { text, tone } = gateStatus(gate);
  return (
    <li className="flex items-center gap-2 px-1.5 py-0.5 text-xs" title={gate.paths.join(", ")}>
      <span aria-hidden className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${DOT[tone]}`} />
      <span className="min-w-0 flex-1 truncate font-mono text-[var(--color-text-primary)]">
        {gate.name}
      </span>
      <span className="shrink-0 text-[var(--color-text-secondary)]">
        {text}
        {gate.informational ? " (informational)" : ""}
      </span>
      <span className="shrink-0 font-mono tabular-nums text-[var(--color-text-tertiary)]">
        {gateFigures(gate)}
      </span>
    </li>
  );
}

function UncoveredList({
  files,
  onOpenFile,
}: {
  files: PatchCoverageFile[];
  onOpenFile: OpenFile;
}) {
  if (!files.length) return null;
  return (
    <ul className="mt-1 flex flex-col gap-0.5">
      {files.slice(0, MAX_FILES).map((f) => (
        <FileRow
          key={f.file_path}
          file={f}
          trailing={`lines ${formatLineRanges(f.uncovered_ranges)}`}
          hint={firstHint(f)}
          onOpenFile={onOpenFile}
        />
      ))}
      {files.length > MAX_FILES && (
        <li className="px-1.5 pt-1 text-xs text-[var(--color-text-tertiary)]">
          and {files.length - MAX_FILES} more
        </li>
      )}
    </ul>
  );
}

function UnmeasuredList({
  files,
  onOpenFile,
}: {
  files: PatchCoverageFile[];
  onOpenFile: OpenFile;
}) {
  if (!files.length) return null;
  return (
    <details className="group mt-1">
      <summary className="cursor-pointer text-xs text-[var(--color-text-tertiary)]">
        {plural(files.length, "changed file")} the coverage report does not measure
      </summary>
      <ul className="mt-1 flex flex-col gap-0.5">
        {files.map((f) => (
          <FileRow
            key={f.file_path}
            file={f}
            trailing={f.status === "not_in_report" ? "not in report" : "no line data"}
            onOpenFile={onOpenFile}
          />
        ))}
      </ul>
    </details>
  );
}

function FileRow({
  file,
  trailing,
  hint = null,
  onOpenFile,
}: {
  file: PatchCoverageFile;
  trailing: string;
  hint?: PatchCoverageTestHint | null;
  onOpenFile: OpenFile;
}) {
  const body = (
    <>
      <span className="min-w-0 flex-1 truncate font-mono text-xs text-[var(--color-text-primary)]">
        {file.file_path}
      </span>
      <RiskLabel risk={file.risk} />
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
      {hint && <HintLine hint={hint} />}
    </li>
  );
}

/** Where to add the missing test: the file's first hint, one line under its row. */
function HintLine({ hint }: { hint: PatchCoverageTestHint }) {
  const test = hint.tests[0];
  return (
    <p
      title={hintText(hint)}
      className="truncate px-1.5 pb-1 text-xs text-[var(--color-text-tertiary)]"
    >
      {test ? (
        <>
          extend <span className="font-mono">{test}</span> · {hintReason(hint)}
        </>
      ) : (
        hintReason(hint)
      )}
    </p>
  );
}

/** A row's risk in words; nothing for "none known", which is not worth a label. */
function RiskLabel({ risk }: { risk: PatchCoverageFile["risk"] | undefined }) {
  const words = riskWords(risk);
  if (words == null || words === "none known") return null;
  const risky = risk?.risky === true;
  const tone = risky ? "text-[var(--color-warning)]" : "text-[var(--color-text-tertiary)]";
  return (
    <span className={`shrink-0 text-[10px] ${tone}`}>
      {risky && <span className="sr-only">Risky: </span>}
      {words}
    </span>
  );
}
