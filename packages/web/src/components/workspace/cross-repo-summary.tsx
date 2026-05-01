"use client";

import { Link2, GitMerge, Package } from "lucide-react";
import type { WorkspaceCrossRepoSummary, WorkspaceContractSummary } from "@/lib/api/types";
import { StatCard } from "@repowise/ui/shared/stat-card";

interface CrossRepoSummaryProps {
  crossRepo: WorkspaceCrossRepoSummary | null;
  contracts: WorkspaceContractSummary | null;
}

export function CrossRepoSummary({ crossRepo, contracts }: CrossRepoSummaryProps) {
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <StatCard
        label="Co-Change Pairs"
        value={crossRepo?.co_change_count ?? 0}
        icon={<GitMerge className="h-4 w-4 text-[var(--color-accent-primary)]" />}
      />
      <StatCard
        label="Package Deps"
        value={crossRepo?.package_dep_count ?? 0}
        icon={<Package className="h-4 w-4 text-purple-400" />}
      />
      <StatCard
        label="Contract Links"
        value={contracts?.total_links ?? 0}
        icon={<Link2 className="h-4 w-4 text-blue-400" />}
      />
      <StatCard
        label="Contracts Detected"
        value={contracts?.total_contracts ?? 0}
        description={
          contracts?.by_type
            ? Object.entries(contracts.by_type)
                .map(([k, v]) => `${v} ${k}`)
                .join(", ")
            : undefined
        }
        icon={<Link2 className="h-4 w-4 text-orange-400" />}
      />
    </div>
  );
}
