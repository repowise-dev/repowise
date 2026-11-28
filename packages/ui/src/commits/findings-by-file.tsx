import type { ReactNode } from "react";
import { SeverityMark } from "../health/severity-mark";
import type { Severity } from "../health/tokens";
import { disambiguateBasenames } from "../lib/format";
import type { CommitHealthFinding } from "@repowise-dev/types/git";
import { attributionKind, groupFindingsByFile, movedFromLabel } from "./health-delta";

export interface FileFindings<F> {
  path: string;
  findings: F[];
}

export interface FindingsByFileLabels {
  count: (n: number) => string;
  more: (files: number) => string;
  was: (severity: string) => string;
  /** Said of a finding in a file the change touched but on a line it did not write. */
  indirect: string;
  /** Said of a finding whose code the change carried here from *from* (`symbol in path`). */
  moved: (from: string | null) => string;
  /** Said of a finding reached through a call the change edited. */
  callEdge: string;
  /** Said of a finding nothing in the change's diff explains. */
  unexplained: string;
}

const DEFAULT_LABELS: FindingsByFileLabels = {
  count: (n) => `${n} finding${n === 1 ? "" : "s"}`,
  more: (n) => `Show ${n} more file${n === 1 ? "" : "s"}`,
  was: (s) => `was ${s}`,
  indirect: "file touched, not a line it wrote",
  moved: (from) => (from ? `moved here from ${from}` : "moved here from another file"),
  callEdge: "reached through a call it edited",
  unexplained: "not explained by its diff",
};

function basisNote(f: CommitHealthFinding, l: FindingsByFileLabels): string | null {
  switch (attributionKind(f)) {
    case "direct":
      return null;
    case "moved":
      return l.moved(movedFromLabel(f));
    case "call_edge":
      return l.callEdge;
    case "unexplained":
      return l.unexplained;
    default:
      return l.indirect;
  }
}

export interface FindingsByFileProps<F extends CommitHealthFinding> {
  /** Worst first; files keep the order of their worst finding. */
  findings: F[];
  /** The quiet per-file action, e.g. a prompt for this file's findings. */
  fileAction?: (group: FileFindings<F>) => ReactNode;
  /** Files shown before the rest fold behind "Show N more files". */
  visibleFiles?: number;
  labels?: Partial<FindingsByFileLabels>;
}

function FindingRow({ f, l }: { f: CommitHealthFinding; l: FindingsByFileLabels }) {
  const where = [f.symbol, f.line_start ? `L${f.line_start}` : null].filter(Boolean).join(" · ");
  const note = basisNote(f, l);
  return (
    <li className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 py-1.5">
      <SeverityMark severity={f.severity as Severity} className="w-16" />
      <span className="min-w-[12rem] flex-1 text-xs text-[var(--color-text-primary)] [overflow-wrap:anywhere]">
        {f.reason}
        {f.change_kind === "worsened" && f.severity_before && (
          <span className="text-[var(--color-text-tertiary)]"> ({l.was(f.severity_before)})</span>
        )}
        {note && <span className="text-[var(--color-text-tertiary)]"> · {note}</span>}
      </span>
      {where && (
        <span className="font-mono text-[11px] text-[var(--color-text-tertiary)] [overflow-wrap:anywhere]">
          {where}
        </span>
      )}
    </li>
  );
}

function FileGroup<F extends CommitHealthFinding>({
  group,
  label,
  action,
  l,
}: {
  group: FileFindings<F>;
  label: string;
  action: ReactNode;
  l: FindingsByFileLabels;
}) {
  return (
    // `group/row` so a per-row prompt action reveals on hover and focus within.
    <section className="group/row py-3">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="min-w-0 font-mono text-xs text-[var(--color-text-primary)] [overflow-wrap:anywhere]" title={group.path}>
          {label}
        </span>
        <span className="text-xs text-[var(--color-text-tertiary)]">{l.count(group.findings.length)}</span>
        {action && <span className="ml-auto">{action}</span>}
      </div>
      <ul className="mt-1">
        {group.findings.map((f) => (
          <FindingRow key={`${f.line_start ?? 0}:${f.biomarker_type}:${f.symbol ?? ""}`} f={f} l={l} />
        ))}
      </ul>
    </section>
  );
}

/**
 * Code-health findings grouped by file: a header per file (shortest label
 * that tells the files apart, full path on hover) and one line per finding
 * with its reason and `symbol · line`. The first few files are open; the rest
 * fold behind one native disclosure, so this stays a server component.
 */
export function FindingsByFile<F extends CommitHealthFinding>({
  findings,
  fileAction,
  visibleFiles = 3,
  labels,
}: FindingsByFileProps<F>) {
  if (findings.length === 0) return null;
  const l = { ...DEFAULT_LABELS, ...labels };
  const groups = groupFindingsByFile(findings);
  const names = disambiguateBasenames(groups.map((g) => g.path));
  const render = (g: FileFindings<F>) => (
    <FileGroup key={g.path} group={g} label={names.get(g.path) ?? g.path} action={fileAction?.(g)} l={l} />
  );
  const shown = groups.slice(0, visibleFiles);
  const rest = groups.slice(visibleFiles);
  return (
    <div className="divide-y divide-[var(--color-border-default)]">
      {shown.map(render)}
      {rest.length > 0 && (
        <details className="group/more py-3">
          <summary className="cursor-pointer text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]">
            {l.more(rest.length)}
          </summary>
          <div className="mt-1 divide-y divide-[var(--color-border-default)]">{rest.map(render)}</div>
        </details>
      )}
    </div>
  );
}
