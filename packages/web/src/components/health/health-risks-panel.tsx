"use client";

import Link from "next/link";
import useSWR from "swr";
import { HealthBadge } from "@repowise-dev/ui/health";
import {
  getHealthOverview,
  type HealthOverviewResponse,
} from "@/lib/api/code-health";

export interface HealthRisksPanelProps {
  repoId: string;
  title?: string;
  limit?: number;
}

/** Inline health-risk sidecar: lowest-scoring files with badges.
 * Page-level only — does not modify any shared list/table component. */
export function HealthRisksPanel({
  repoId,
  title = "Health risks",
  limit = 6,
}: HealthRisksPanelProps) {
  const { data } = useSWR<HealthOverviewResponse>(
    `health-overview-panel:${repoId}`,
    () => getHealthOverview(repoId, limit),
    { revalidateOnFocus: false },
  );
  if (!data || data.files.length === 0) return null;
  const rows = [...data.files]
    .sort((a, b) => a.score - b.score)
    .slice(0, limit);
  return (
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3 space-y-2">
      <div className="flex items-center justify-between text-xs">
        <span className="font-semibold text-[var(--color-text-primary)]">
          {title}
        </span>
        <Link
          href={`/repos/${repoId}/code-health`}
          className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
        >
          See all →
        </Link>
      </div>
      <ul className="space-y-1.5">
        {rows.map((f) => (
          <li
            key={f.file_path}
            className="flex items-center gap-2 text-xs"
          >
            <HealthBadge score={f.score} />
            <span
              className="font-mono truncate text-[var(--color-text-secondary)]"
              title={f.file_path}
            >
              {f.file_path}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
