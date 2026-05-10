"use client";

import useSWR from "swr";
import { Flame, Users, Trash2, Lightbulb, ShieldAlert } from "lucide-react";
import { StatCard } from "@repowise-dev/ui/shared/stat-card";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import { getGitSummary, getOwnership } from "@/lib/api/git";
import { getDeadCodeSummary } from "@/lib/api/dead-code";
import { getDecisionHealth } from "@/lib/api/decisions";
import { listSecurityFindings } from "@/lib/api/security";

/**
 * Always-visible 5-card strip atop /repos/[id]/risk. Pulls cached SWR data
 * shared with the tab bodies, so switching tabs doesn't trigger refetches.
 */
export function RiskSummaryStrip({ repoId }: { repoId: string }) {
  const { data: gitSummary } = useSWR(`git-summary:${repoId}`, () => getGitSummary(repoId), {
    revalidateOnFocus: false,
  });
  const { data: ownership } = useSWR(`ownership:${repoId}:module`, () =>
    getOwnership(repoId, "module"),
  );
  const { data: deadCode } = useSWR(`dead-code-summary:${repoId}`, () =>
    getDeadCodeSummary(repoId),
  );
  const { data: decisionHealth } = useSWR(
    `decision-health:${repoId}`,
    () => getDecisionHealth(repoId),
    { revalidateOnFocus: false },
  );
  const { data: securityFindings } = useSWR(
    `security-findings:${repoId}`,
    // Pull a tight slice; we only need the count for the badge here.
    () => listSecurityFindings(repoId, { limit: 200 }).catch(() => [] as Awaited<ReturnType<typeof listSecurityFindings>>),
    { revalidateOnFocus: false },
  );

  const siloCount = (ownership ?? []).filter((o) => o.is_silo).length;
  const reclaimable = deadCode?.deletable_lines ?? 0;
  const staleDecisions = decisionHealth?.stale_decisions?.length ?? 0;
  const securityCritical = (securityFindings ?? []).filter(
    (f) => f.severity === "high" || f.severity === "critical",
  ).length;

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      <StatCard
        label="Hotspots"
        value={formatNumber(gitSummary?.hotspot_count ?? 0)}
        description="high-churn files"
        icon={<Flame className="h-4 w-4 text-orange-400" />}
      />
      <StatCard
        label="Silos"
        value={formatNumber(siloCount)}
        description="single-owner modules"
        icon={<Users className="h-4 w-4 text-amber-400" />}
      />
      <StatCard
        label="Reclaimable"
        value={formatNumber(reclaimable)}
        description="lines safe to delete"
        icon={<Trash2 className="h-4 w-4 text-rose-400" />}
      />
      <StatCard
        label="Stale Decisions"
        value={formatNumber(staleDecisions)}
        description="needing review"
        icon={<Lightbulb className="h-4 w-4 text-yellow-400" />}
      />
      <StatCard
        label="Security"
        value={securityFindings ? formatNumber(securityCritical) : "—"}
        description={
          securityFindings
            ? "critical / high findings"
            : "scan not run"
        }
        icon={<ShieldAlert className="h-4 w-4 text-red-400" />}
      />
    </div>
  );
}
