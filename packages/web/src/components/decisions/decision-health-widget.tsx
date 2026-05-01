"use client";

import useSWR from "swr";
import { getDecisionHealth } from "@/lib/api/decisions";
import { StatCard } from "@repowise/ui/shared/stat-card";

interface DecisionHealthWidgetProps {
  repoId: string;
}

export function DecisionHealthWidget({ repoId }: DecisionHealthWidgetProps) {
  const { data: health } = useSWR(
    `/api/repos/${repoId}/decisions/health`,
    () => getDecisionHealth(repoId),
  );

  if (!health) return null;

  const { summary } = health;

  return (
    <div className="grid grid-cols-3 gap-3">
      <StatCard
        label="Active Decisions"
        value={summary.active}
      />
      <StatCard
        label="Proposed"
        value={summary.proposed}
      />
      <StatCard
        label="Stale"
        value={summary.stale}
      />
    </div>
  );
}
