"use client";

import type { DirectRiskEntry } from "@repowise-dev/types/blast-radius";
import { ResponsiveTable, type ResponsiveColumn } from "../shared/responsive-table";
import { riskInk } from "../health/tokens";

interface DirectRisksTableProps {
  rows: DirectRiskEntry[];
}

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

const COLUMNS: ResponsiveColumn<DirectRiskEntry>[] = [
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
    key: "risk_score",
    header: "Risk (0–10)",
    headerClassName: "w-[28%]",
    render: (r) => (
      <MiniBar
        value01={r.risk_score}
        color={riskInk(r.risk_score)}
        display={(r.risk_score * 10).toFixed(1)}
      />
    ),
    mobileRender: (r) => (r.risk_score * 10).toFixed(1),
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
 * Direct dependents of the changed files, sorted by risk. Each numeric column
 * is an inline mini-bar (risk health-banded, hotspot/centrality neutral) so the
 * heaviest rows pop without reading every figure.
 */
export function DirectRisksTable({ rows }: DirectRisksTableProps) {
  const sorted = [...rows].sort((a, b) => b.risk_score - a.risk_score);
  return (
    <ResponsiveTable
      columns={COLUMNS}
      rows={sorted}
      rowKey={(r) => r.path}
      caption="Direct risks for the changed files"
      bare
    />
  );
}
