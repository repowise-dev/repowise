"use client";

import type { TransitiveEntry } from "@repowise-dev/types/blast-radius";
import { ResponsiveTable, type ResponsiveColumn } from "../shared/responsive-table";
import { FilePath } from "../shared/file-path";

interface TransitiveTableProps {
  rows: TransitiveEntry[];
}

const COLUMNS: ResponsiveColumn<TransitiveEntry>[] = [
  {
    key: "path",
    header: "File",
    render: (r) => (
      <FilePath path={r.path} fold className="max-w-[28rem]" />
    ),
  },
  {
    key: "depth",
    header: "Depth",
    align: "right",
    render: (r) => <span className="tabular-nums">{r.depth}</span>,
  },
];

export function TransitiveTable({ rows }: TransitiveTableProps) {
  return (
    <ResponsiveTable
      columns={COLUMNS}
      rows={rows}
      rowKey={(r) => r.path}
      caption="Transitively affected files"
      bare
    />
  );
}
