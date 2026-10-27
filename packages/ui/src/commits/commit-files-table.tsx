"use client";

import { ResponsiveTable, type ResponsiveColumn } from "../shared/responsive-table";
import { formatLOC } from "../lib/format";
import type { CommitFile } from "@repowise-dev/types/git";

export interface CommitFilesTableProps {
  files: CommitFile[];
  /** What touched the files, as the caption names it. */
  noun?: string;
  className?: string;
}

const PATH: ResponsiveColumn<CommitFile> = {
  key: "path",
  header: "File",
  cellClassName: "font-mono text-[11px] [overflow-wrap:anywhere]",
  render: (f) => f.path,
};

const LINES: ResponsiveColumn<CommitFile> = {
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
};

const PRIOR_FIXES: ResponsiveColumn<CommitFile> = {
  key: "prior_fixes",
  header: "Prior fixes",
  align: "right",
  headerClassName: "w-24",
  // A dash is "the repo no longer tracks this", not zero.
  render: (f) => <span className="tabular-nums">{f.prior_fixes ?? "—"}</span>,
};

/**
 * The files a commit touched, and the fix record each carries. Leads the sheet
 * because, unlike the percentile, it does not grow with the diff. A caller
 * with no fix record at all (the field absent on every row) gets no column,
 * rather than a column of dashes that would read as "untracked".
 */
export function CommitFilesTable({ files, noun = "commit", className }: CommitFilesTableProps) {
  if (files.length === 0) return null;
  const withFixes = files.some((f) => f.prior_fixes !== undefined);

  return (
    <ResponsiveTable
      className={className}
      columns={withFixes ? [PATH, LINES, PRIOR_FIXES] : [PATH, LINES]}
      rows={files}
      rowKey={(f) => f.path}
      caption={
        withFixes
          ? `Files this ${noun} touched, and how much bug-fix history each carries`
          : `Files this ${noun} touched`
      }
      stacked="sm"
      bare
    />
  );
}
