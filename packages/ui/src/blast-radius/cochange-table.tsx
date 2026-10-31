"use client";

import type { CochangeWarning } from "@repowise-dev/types/blast-radius";
import { ResponsiveTable, type ResponsiveColumn } from "../shared/responsive-table";
import { FilePath } from "../shared/file-path";

interface CochangeTableProps {
  rows: CochangeWarning[];
}

const COLUMNS: ResponsiveColumn<CochangeWarning>[] = [
  {
    key: "changed",
    header: "Changed file",
    render: (r) => <FilePath path={r.changed} copyable />,
  },
  {
    key: "missing_partner",
    header: "Usually changes with it",
    render: (r) => <FilePath path={r.missing_partner} copyable />,
  },
  {
    key: "score",
    header: "Times together",
    align: "right",
    render: (r) => <span className="tabular-nums">{r.score}</span>,
  },
];

export function CochangeTable({ rows }: CochangeTableProps) {
  const keyed = rows.map((row, i) => ({
    ...row,
    _key: `${row.changed}|${row.missing_partner}|${i}`,
  }));
  return (
    <ResponsiveTable<CochangeWarning & { _key: string }>
      columns={COLUMNS}
      rows={keyed}
      rowKey={(r) => r._key}
      caption="Co-change warnings"
      bare
    />
  );
}
