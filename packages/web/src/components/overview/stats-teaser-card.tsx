import Link from "next/link";
import { BarChart3, ArrowRight, Bot } from "lucide-react";
import type { StatsHighlights } from "@repowise-dev/types/stats";
import { Card } from "@repowise-dev/ui/ui/card";
import { formatLOC, formatNumber, formatAgeDays } from "@repowise-dev/ui/lib/format";

interface StatsTeaserCardProps {
  repoId: string;
  data: StatsHighlights;
}

/** Compact overview teaser that headlines the "size class" + a couple of
 *  marquee figures and links into the full Stats page. */
export function StatsTeaserCard({ repoId, data }: StatsTeaserCardProps) {
  const { scale, activity } = data;

  const figures: { label: string; value: string }[] = [
    { label: "Lines", value: formatLOC(scale.total_nloc) },
    { label: "Commits", value: formatNumber(activity.total_commits) },
    {
      label: "Age",
      value: activity.age_days != null ? formatAgeDays(activity.age_days) : "—",
    },
  ];

  return (
    <Card className="overflow-hidden">
      <Link
        href={`/repos/${repoId}/stats`}
        className="group block p-4 transition-colors hover:bg-[var(--color-bg-elevated)]"
      >
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-2 text-sm font-semibold text-[var(--color-text-primary)]">
            <BarChart3 className="h-4 w-4 text-[var(--color-accent-primary)]" />
            By the Numbers
          </span>
          <ArrowRight className="h-4 w-4 text-[var(--color-text-tertiary)] transition-transform group-hover:translate-x-0.5" />
        </div>

        <div className="mt-3 flex items-baseline gap-2">
          <span className="text-2xl font-bold text-[var(--color-text-primary)]">
            {scale.size_class.name}
          </span>
          <span className="text-xs text-[var(--color-text-tertiary)]">
            {formatNumber(scale.file_count)} files
          </span>
        </div>

        <div className="mt-3 grid grid-cols-3 gap-2">
          {figures.map((f) => (
            <div key={f.label}>
              <p className="text-base font-semibold tabular-nums text-[var(--color-text-primary)]">
                {f.value}
              </p>
              <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
                {f.label}
              </p>
            </div>
          ))}
        </div>

        {activity.total_commits > 0 && (
          <p className="mt-3 flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
            <Bot className="h-3.5 w-3.5 text-[var(--color-info)]" />
            {activity.agent_pct}% of commits agent-authored
          </p>
        )}
      </Link>
    </Card>
  );
}
