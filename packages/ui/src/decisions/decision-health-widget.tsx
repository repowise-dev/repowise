"use client";

import { StatCard } from "../shared/stat-card";
import type { DecisionHealth } from "@repowise-dev/types/decisions";

export interface DecisionHealthWidgetProps {
  /** Resolved health summary; widget renders nothing while undefined. */
  health: DecisionHealth | undefined;
}

export function DecisionHealthWidget({ health }: DecisionHealthWidgetProps) {
  if (!health) return null;

  const { summary } = health;

  return (
    <div className="grid grid-cols-3 gap-3">
      <StatCard label="Active Decisions" value={summary.active} />
      <StatCard label="Proposed" value={summary.proposed} />
      <StatCard label="Stale" value={summary.stale} />
    </div>
  );
}
