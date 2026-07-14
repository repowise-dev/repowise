import { Bot, CalendarClock, Users, ShieldCheck, Trash2, HeartPulse, Package } from "lucide-react";
import type { StatsHighlights } from "@repowise-dev/types/stats";
import {
  SizeClassHero,
  StatCallout,
  SuperlativesGrid,
  NLOC_HINT,
  AGENT_PCT_HINT,
} from "@repowise-dev/ui/stats";
import { formatNumber, formatLOC, formatAgeDays, formatDate } from "@repowise-dev/ui/lib/format";

const ECOSYSTEM_NAMES: Record<string, string> = {
  npm: "npm",
  pypi: "PyPI",
  go: "Go",
  cargo: "crates.io",
  nuget: "NuGet",
};

export function ByTheNumbersTab({ data }: { data: StatsHighlights }) {
  const { scale, activity, quality, people, superlatives } = data;
  const da = quality.defect_accuracy;
  const deps = data.dependencies;

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
          )} commits carry a verifiable agent signature`}
          hint={AGENT_PCT_HINT}
        />
        <StatCallout
          label="Project age"
          value={activity.age_days != null ? formatAgeDays(activity.age_days) : "—"}
          icon={<CalendarClock className="h-4 w-4" />}
          sub={
            activity.first_commit_at
              ? `since ${formatDate(activity.first_commit_at)}${
                  activity.first_commit_author ? ` · started by ${activity.first_commit_author}` : ""
                }`
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

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCallout
          label="Lines of code"
          value={formatLOC(scale.total_nloc)}
          sub={`in the repo today · ${formatNumber(scale.symbol_count)} symbols across ${formatNumber(
            scale.module_count,
          )} modules`}
          hint={NLOC_HINT}
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
        {deps && deps.total > 0 && (
          <StatCallout
            label="Dependencies"
            value={formatNumber(deps.total)}
            icon={<Package className="h-4 w-4" />}
            sub={`standing on ${formatNumber(deps.runtime)} runtime + ${formatNumber(
              deps.dev,
            )} dev shoulders across ${deps.ecosystems
              .map((e) => ECOSYSTEM_NAMES[e.name] ?? e.name)
              .join(", ")}`}
          />
        )}
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
