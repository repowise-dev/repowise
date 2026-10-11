import { ResponsiveTable, type ResponsiveColumn } from "../shared/responsive-table";
import { SeverityMark } from "../health/severity-mark";
import type { Severity } from "../health/tokens";
import type { CommitHealthFinding } from "@repowise-dev/types/git";

export interface CommitHealthTableProps {
  findings: CommitHealthFinding[];
  className?: string;
}

/** Bases where the change wrote the code the finding sits on. The rest only
 *  touched the file, which is a weaker claim and is labelled as one. */
const DIRECT = new Set(["added_lines", "changed_symbol", "new_file"]);

function where(f: CommitHealthFinding): string {
  const at = f.line_start ? `:${f.line_start}` : "";
  return f.symbol ? `${f.symbol} — ${f.path}${at}` : `${f.path}${at}`;
}

const COLUMNS: ResponsiveColumn<CommitHealthFinding>[] = [
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
    render: (f) => (
      <span>
        {f.reason}
        {!DIRECT.has(f.attribution_basis) && (
          <span className="text-[var(--color-text-tertiary)]">
            {" "}
            (in a file this commit touched, not on a line it wrote)
          </span>
        )}
      </span>
    ),
  },
  {
    key: "where",
    header: "Where",
    cellClassName: "font-mono text-[11px] [overflow-wrap:anywhere]",
    headerClassName: "w-64",
    render: where,
  },
];

/**
 * What a commit introduced or worsened, worst first.
 *
 * Only these two kinds are listed. A resolved finding is real good news but has
 * no location worth pointing at any more, so it is counted in the section
 * heading instead of given a row here.
 */
export function CommitHealthTable({ findings, className }: CommitHealthTableProps) {
  if (findings.length === 0) return null;

  return (
    <ResponsiveTable
      className={className}
      columns={COLUMNS}
      rows={findings}
      rowKey={(f) => `${f.path}:${f.line_start ?? 0}:${f.biomarker_type}`}
      caption="Code-health findings this commit introduced or made worse"
      stacked="sm"
      bare
    />
  );
}
