import { ResponsiveTable, type ResponsiveColumn } from "../shared/responsive-table";
import { formatLOC } from "../lib/format";
import type { CommitFile } from "@repowise-dev/types/git";

export interface CommitFilesTableProps {
  files: CommitFile[];
  className?: string;
}

const COLUMNS: ResponsiveColumn<CommitFile>[] = [
  {
    key: "path",
    header: "File",
    cellClassName: "font-mono text-[11px] [overflow-wrap:anywhere]",
    render: (f) => f.path,
  },
  {
    key: "lines",
    header: "Lines",
    align: "right",
    headerClassName: "w-28",
    render: (f) => (
      <span className="tabular-nums">
        <span className="text-[var(--color-success)]">+{formatLOC(f.lines_added)}</span>{" "}
        <span className="text-[var(--color-error)]">-{formatLOC(f.lines_deleted)}</span>
      </span>
    ),
  },
  {
    key: "prior_fixes",
    header: "Prior fixes",
    align: "right",
    headerClassName: "w-24",
    // A dash is "the repo no longer tracks this", not zero.
    render: (f) => <span className="tabular-nums">{f.prior_fixes ?? "—"}</span>,
  },
];

/**
 * The files a commit touched, and the fix record each carries. Leads the sheet
 * because, unlike the percentile, it does not grow with the diff.
 */
export function CommitFilesTable({ files, className }: CommitFilesTableProps) {
  if (files.length === 0) return null;

  return (
    <ResponsiveTable
      className={className}
      columns={COLUMNS}
      rows={files}
      rowKey={(f) => f.path}
      caption="Files this commit touched, and how much bug-fix history each carries"
      stacked="sm"
      bare
    />
  );
}
