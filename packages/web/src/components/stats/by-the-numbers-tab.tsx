import Link from "next/link";
import {
  Bot,
  CalendarClock,
  Users,
  ShieldCheck,
  Trash2,
  HeartPulse,
  Package,
  TrendingUp,
  TrendingDown,
  Sparkles,
} from "lucide-react";
import type { StatsHighlights } from "@repowise-dev/types/stats";
import {
  SizeClassHero,
  StatCallout,
  SuperlativesGrid,
  PunchCard,
  AGENT_PCT_HINT,
} from "@repowise-dev/ui/stats";
import {
  formatNumber,
  formatAgeDays,
  formatDate,
  formatTokens,
  formatCost,
} from "@repowise-dev/ui/lib/format";
import { useWeekendDays } from "@/lib/hooks/use-weekend";

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
  const build = data.build;
  const vel = activity.velocity;
  const weekendDays = useWeekendDays();

  // Momentum: recent-vs-prior commit change, or a plain recent count when there
  // is no prior window to compare against (young repo).
  const rising = vel?.pct_change != null && vel.pct_change >= 0;
  const momentum =
    vel?.pct_change != null
      ? `${rising ? "+" : ""}${vel.pct_change}%`
      : formatNumber(vel?.recent_90d ?? 0);

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

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <PunchCard data={activity.punch_card} weekendDays={weekendDays} />
        </div>
        <div className="flex flex-col gap-4">
          {/* Momentum — recent vs prior 90-day commit volume, with a mini
              comparison bar so the card carries its own weight. */}
          <div className="flex flex-1 flex-col justify-between rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4">
            <div className="flex items-center justify-between gap-2">
              <p className="text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
                Momentum
              </p>
              <span
                className={
                  vel?.pct_change == null
                    ? "text-[var(--color-text-tertiary)]"
                    : rising
                      ? "text-[var(--color-success)]"
                      : "text-[var(--color-warning)]"
                }
              >
                {vel?.pct_change == null ? null : rising ? (
                  <TrendingUp className="h-4 w-4" />
                ) : (
                  <TrendingDown className="h-4 w-4" />
                )}
              </span>
            </div>
            <p
              className={`mt-1.5 text-2xl font-bold leading-none tabular-nums ${
                vel?.pct_change == null
                  ? "text-[var(--color-text-primary)]"
                  : rising
                    ? "text-[var(--color-success)]"
                    : "text-[var(--color-warning)]"
              }`}
            >
              {momentum}
            </p>
            {vel && vel.pct_change != null ? (
              <div className="mt-4 space-y-2">
                {(
                  [
                    { label: "Prev 90d", value: vel.prior_90d, accent: false },
                    { label: "Last 90d", value: vel.recent_90d, accent: true },
                  ] as const
                ).map((r) => {
                  const barMax = Math.max(vel.recent_90d, vel.prior_90d, 1);
                  return (
                    <div key={r.label} className="flex items-center gap-2 text-[11px]">
                      <span className="w-14 shrink-0 text-[var(--color-text-tertiary)]">
                        {r.label}
                      </span>
                      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[var(--color-bg-muted)]">
                        <div
                          className="h-full rounded-full"
                          style={{
                            width: `${(r.value / barMax) * 100}%`,
                            background: r.accent
                              ? "var(--color-accent-primary)"
                              : "var(--color-text-tertiary)",
                          }}
                        />
                      </div>
                      <span className="w-8 shrink-0 text-right tabular-nums text-[var(--color-text-secondary)]">
                        {formatNumber(r.value)}
                      </span>
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="mt-2 text-xs text-[var(--color-text-secondary)]">
                commits in the last 90 days
              </p>
            )}
          </div>

          {/* This wiki, by the numbers — the knowledge base bragging about
              itself. Spread top-to-bottom so it fills the column. */}
          {build && build.page_count > 0 && (
            <div
              className="flex flex-1 flex-col justify-between overflow-hidden rounded-xl border border-[var(--color-border-default)] p-4"
              style={{ background: "var(--gradient-warm-wash, var(--color-bg-surface))" }}
            >
              <div className="flex items-center gap-2">
                <Sparkles className="h-4 w-4 text-[var(--color-accent-primary)]" />
                <p className="text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
                  This wiki, by the numbers
                </p>
              </div>
              <div className="my-3 grid grid-cols-3 gap-2">
                {[
                  { v: formatNumber(build.page_count), l: "pages" },
                  { v: formatTokens(build.total_tokens), l: "tokens" },
                  { v: formatCost(build.cost_usd), l: "to build" },
                ].map((s) => (
                  <div key={s.l}>
                    <p className="text-lg font-semibold tabular-nums text-[var(--color-text-primary)]">
                      {s.v}
                    </p>
                    <p className="mt-0.5 text-[11px] text-[var(--color-text-tertiary)]">{s.l}</p>
                  </div>
                ))}
              </div>
              <p className="text-[11px] text-[var(--color-text-secondary)]">
                distilled from your codebase in {formatNumber(build.llm_operations)} model calls
              </p>
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
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
            href={`/repos/${data.repo.id}/architecture?view=deps`}
            LinkComponent={Link}
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
