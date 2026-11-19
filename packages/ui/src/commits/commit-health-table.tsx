"use client";

import { ResponsiveTable, type ResponsiveColumn } from "../shared/responsive-table";
import { SeverityMark } from "../health/severity-mark";
import type { Severity } from "../health/tokens";
import type { CommitHealthFinding } from "@repowise-dev/types/git";
import { attributionKind, movedFromLabel } from "./health-delta";

export interface CommitHealthTableProps {
  findings: CommitHealthFinding[];
  /** What the findings belong to, as the copy names it: a commit, or a
   *  merged change made of several. */
  noun?: string;
  className?: string;
}


/** How the change relates to the finding, when it did not write the line itself. */
function basisNote(f: CommitHealthFinding, noun: string): string | null {
  switch (attributionKind(f)) {
    case "direct":
      return null;
    case "moved": {
      const from = movedFromLabel(f);
      return from ? `moved here from ${from}` : "moved here from another file";
    }
    case "call_edge":
      return `reached through a call this ${noun} edited`;
    case "unexplained":
      return `not explained by this ${noun}'s diff`;
    default:
      return `in a file this ${noun} touched, not on a line it wrote`;
  }
}

function where(f: CommitHealthFinding): string {
  const at = f.line_start ? `:${f.line_start}` : "";
  return f.symbol ? `${f.symbol} — ${f.path}${at}` : `${f.path}${at}`;
}

function columns(noun: string): ResponsiveColumn<CommitHealthFinding>[] {
  return [
    {
      key: "severity",
      header: "Severity",
      headerClassName: "w-24",
      render: (f) => (
        <span className="inline-flex flex-col gap-0.5">
          <SeverityMark severity={f.severity as Severity} />
          {f.change_kind === "worsened" && f.severity_before && (
            <span className="text-[10px] text-[var(--color-text-tertiary)]">
              was {f.severity_before}
            </span>
          )}
        </span>
      ),
    },
    {
      key: "reason",
      header: "What changed",
      cellClassName: "[overflow-wrap:anywhere]",
      render: (f) => {
        const note = basisNote(f, noun);
        return (
          <span>
            {f.reason}
            {note && <span className="text-[var(--color-text-tertiary)]"> ({note})</span>}
          </span>
        );
      },
    },
    {
      key: "where",
      header: "Where",
      cellClassName: "font-mono text-[11px] [overflow-wrap:anywhere]",
      headerClassName: "w-64",
      render: where,
    },
  ];
}

/**
 * What a commit (or a merged change) introduced or worsened, worst first.
 *
 * Only these two kinds are listed. A resolved finding is real good news but has
 * no location worth pointing at any more, so it is counted in the section
 * heading instead of given a row here.
 */
export function CommitHealthTable({ findings, noun = "commit", className }: CommitHealthTableProps) {
  if (findings.length === 0) return null;

  return (
    <ResponsiveTable
      className={className}
      columns={columns(noun)}
      rows={findings}
      rowKey={(f) => `${f.path}:${f.line_start ?? 0}:${f.biomarker_type}:${f.symbol ?? ""}`}
      caption={`Code-health findings this ${noun} introduced or made worse`}
      stacked="sm"
      bare
    />
  );
}
