"use client";

import type { ReviewerEntry } from "@repowise-dev/types/blast-radius";
import { ResponsiveTable, type ResponsiveColumn } from "../shared/responsive-table";

interface ReviewersTableProps {
  rows: ReviewerEntry[];
}

const COLUMNS: ResponsiveColumn<ReviewerEntry>[] = [
  {
    key: "email",
    header: "Email",
    render: (r) => r.email,
  },
  {
    key: "files",
    header: "Files Owned",
    align: "right",
    render: (r) => <span className="tabular-nums">{r.files}</span>,
  },
  {
    key: "ownership_pct",
    header: "Avg Ownership %",
    align: "right",
    render: (r) => (
      <span className="tabular-nums">{(r.ownership_pct * 100).toFixed(1)}%</span>
    ),
  },
];

export function ReviewersTable({ rows }: ReviewersTableProps) {
  return (
    <ResponsiveTable
      columns={COLUMNS}
      rows={rows}
      rowKey={(r) => r.email}
      caption="Recommended reviewers"
      bare
    />
  );
}
