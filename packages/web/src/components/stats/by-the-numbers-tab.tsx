import { Bot, CalendarClock, Users, ShieldCheck, Trash2, HeartPulse } from "lucide-react";
import type { StatsHighlights } from "@repowise-dev/types/stats";
import { SizeClassHero, StatCallout, SuperlativesGrid } from "@repowise-dev/ui/stats";
import { formatNumber, formatLOC, formatAgeDays, formatDate } from "@repowise-dev/ui/lib/format";

export function ByTheNumbersTab({ data }: { data: StatsHighlights }) {
  const { scale, activity, quality, people, superlatives } = data;
  const da = quality.defect_accuracy;

  return (
    <div className="space-y-5">
      <SizeClassHero scale={scale} repoName={data.repo.name} />

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCallout
          label="AI authorship"
          value={`${activity.agent_pct}%`}
          tone="info"
          icon={<Bot className="h-4 w-4" />}
          sub={`${formatNumber(activity.agent_commits)} of ${formatNumber(
            activity.total_commits,
          )} commits written by agents`}
        />
        <StatCallout
          label="Project age"
          value={activity.age_days != null ? formatAgeDays(activity.age_days) : "—"}
          icon={<CalendarClock className="h-4 w-4" />}
          sub={
            activity.first_commit_at
              ? `since ${formatDate(activity.first_commit_at)}`
              : "first commit date unknown"
          }
        />
        <StatCallout
          label="Contributors"
          value={formatNumber(activity.contributor_count)}
          icon={<Users className="h-4 w-4" />}
          sub={`${formatNumber(people.owner_count)} hold primary ownership of a file`}
        />
        {da ? (
          <StatCallout
            label="Score finds bugs"
            value={da.lift != null ? `${da.lift}×` : `${Math.round(da.precision * 100)}%`}
            tone="success"
            icon={<ShieldCheck className="h-4 w-4" />}
            sub={`${da.hits}/${da.k} least-healthy files later got a bug fix — ${
              da.lift != null ? `${da.lift}× the base rate` : "above base rate"
            }`}
          />
        ) : (
          <StatCallout
            label="Defect risk"
            value={quality.average_health != null ? `${quality.average_health.toFixed(1)}/10` : "—"}
            tone="accent"
            icon={<HeartPulse className="h-4 w-4" />}
            sub="average health across all files"
          />
        )}
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatCallout
          label="Total lines"
          value={formatLOC(scale.total_nloc)}
          sub={`${formatNumber(scale.symbol_count)} symbols across ${formatNumber(
            scale.module_count,
          )} modules`}
        />
        <StatCallout
          label="Knowledge captured"
          value={formatNumber(data.knowledge.decision_count)}
          sub={`architectural decisions · ${formatNumber(quality.page_count)} wiki pages`}
        />
        <StatCallout
          label="Recoverable lines"
          value={formatNumber(quality.dead_code.deletable_lines)}
          tone="warning"
          icon={<Trash2 className="h-4 w-4" />}
          sub={`safe to delete across ${formatNumber(
            quality.dead_code.total_findings,
          )} dead-code findings`}
        />
      </div>

      <div>
        <h3 className="mb-2 text-sm font-semibold text-[var(--color-text-primary)]">
          Records & superlatives
        </h3>
        <SuperlativesGrid superlatives={superlatives} />
      </div>
    </div>
  );
}
