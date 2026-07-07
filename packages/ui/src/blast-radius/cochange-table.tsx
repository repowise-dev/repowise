"use client";

import type { CochangeWarning } from "@repowise-dev/types/blast-radius";
import { ResponsiveTable, type ResponsiveColumn } from "../shared/responsive-table";

interface CochangeTableProps {
  rows: CochangeWarning[];
}

const COLUMNS: ResponsiveColumn<CochangeWarning>[] = [
  {
    key: "changed",
    header: "Changed File",
    render: (r) => (
      <span className="font-mono text-xs break-all" title={r.changed}>
        {r.changed}
      </span>
    ),
  },
  {
    key: "missing_partner",
    header: "Missing Partner",
    render: (r) => (
      <span className="font-mono text-xs break-all" title={r.missing_partner}>
        {r.missing_partner}
      </span>
    ),
  },
  {
    key: "score",
    header: "Co-change Count",
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
