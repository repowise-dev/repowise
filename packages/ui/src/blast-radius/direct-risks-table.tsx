"use client";

import type { DirectRiskEntry } from "@repowise-dev/types/blast-radius";
import { ResponsiveTable, type ResponsiveColumn } from "../shared/responsive-table";

interface DirectRisksTableProps {
  rows: DirectRiskEntry[];
}

type DisplayDirectRisk = DirectRiskEntry & { structuralShare: number };

/** A 0–1 value rendered as a labelled mini-bar so rows scan visually. */
function MiniBar({
  value01,
  color,
  display,
}: {
  value01: number;
  color: string;
  display: string;
}) {
  const pct = Math.max(0, Math.min(100, value01 * 100));
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-full min-w-[48px] overflow-hidden rounded-full bg-[var(--color-bg-wash)]">
        <div
          className="h-full rounded-full"
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
      <span className="w-10 shrink-0 text-right tabular-nums text-[var(--color-text-secondary)]">
        {display}
      </span>
    </div>
  );
}

const COLUMNS: ResponsiveColumn<DisplayDirectRisk>[] = [
  {
    key: "path",
    header: "File",
    render: (r) => (
      <span
        className="block max-w-[280px] truncate font-mono text-xs text-[var(--color-text-secondary)]"
        title={r.path}
      >
        {r.path}
      </span>
    ),
  },
  {
    key: "structural_score",
    header: "Structural weight (raw)",
    headerClassName: "w-[28%]",
    render: (r) => (
      <MiniBar
        value01={r.structuralShare}
        color="var(--color-accent-secondary)"
        display={r.structural_score.toFixed(4)}
      />
    ),
    mobileRender: (r) => r.structural_score.toFixed(4),
  },
  {
    key: "temporal_hotspot",
    header: "Temporal hotspot",
    headerClassName: "w-[24%]",
    priority: 2,
    render: (r) => (
      <MiniBar
        value01={r.temporal_hotspot}
        color="var(--color-accent-secondary)"
        display={(r.temporal_hotspot * 10).toFixed(1)}
      />
    ),
    mobileRender: (r) => (r.temporal_hotspot * 10).toFixed(1),
  },
  {
    key: "centrality",
    header: "Centrality",
    headerClassName: "w-[24%]",
    priority: 2,
    render: (r) => (
      <MiniBar
        value01={r.centrality}
        color="var(--color-info)"
        display={`${(r.centrality * 100).toFixed(0)}%`}
      />
    ),
    mobileRender: (r) => `${(r.centrality * 100).toFixed(0)}%`,
  },
];

/**
 * Changed files sorted by raw structural weight. The bar is relative to the
 * strongest file in this change; the displayed value remains the unbounded raw
 * pagerank-weighted heuristic and is never presented as a 0–10 risk score.
 */
export function DirectRisksTable({ rows }: DirectRisksTableProps) {
  const max = rows.reduce((value, row) => Math.max(value, row.structural_score), 0);
  const sorted: DisplayDirectRisk[] = rows
    .map((row) => ({
      ...row,
      structuralShare: max > 0 ? row.structural_score / max : 0,
    }))
    .sort((a, b) => b.structural_score - a.structural_score);
  return (
    <ResponsiveTable
      columns={COLUMNS}
      rows={sorted}
      rowKey={(r) => r.path}
      caption="Structural weights for the changed files"
      bare
    />
  );
}
