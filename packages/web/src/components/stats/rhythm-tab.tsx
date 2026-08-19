"use client";

import { CalendarDays, Flame, Snowflake, TrendingDown, TrendingUp } from "lucide-react";
import type { StatsHighlights } from "@repowise-dev/types/stats";
import { PunchCard, StatCallout } from "@repowise-dev/ui/stats";
import { formatDate, formatNumber } from "@repowise-dev/ui/lib/format";
import { useWeekendDays } from "@/lib/hooks/use-weekend";

function monthLabel(key: string): string {
  const [y, m] = key.split("-");
  // Constructed from parts rather than parsed from a string so the value is
  // never nudged across a month boundary by the browser's offset. Day is
  // pinned to the 1st and only month/year are ever displayed.
  return new Date(Number(y), Number(m) - 1, 1).toLocaleDateString(undefined, {
    month: "short",
    year: "numeric",
  });
}

/**
 * Tab 2 — when the work happens.
 *
 * The one subject with no competing page: Commits ranks individual commits by
 * risk and charts their categories over calendar time, but nothing anywhere
 * else in the app has a clock. Time-of-day, streaks, peaks, and how fast code
 * goes cold all live here.
 */
export function RhythmTab({ data }: { data: StatsHighlights }) {
  const { rhythm } = data;
  const weekendDays = useWeekendDays();
  const vel = rhythm.velocity;

  const rising = vel?.pct_change != null && vel.pct_change >= 0;
  const momentum =
    vel?.pct_change != null
      ? `${rising ? "+" : ""}${vel.pct_change}%`
      : formatNumber(vel?.recent_90d ?? 0);

  return (
    <div className="flex flex-col gap-8">
      <PunchCard
        data={rhythm.punch_card}
        weekendDays={weekendDays}
        firstCommitAt={data.origin.first_commit_at}
        lastCommitAt={data.origin.last_commit_at}
      />

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCallout
          label="Momentum"
          value={momentum}
          icon={rising ? <TrendingUp className="h-4 w-4" /> : <TrendingDown className="h-4 w-4" />}
          tone={vel?.pct_change == null ? "default" : rising ? "success" : "warning"}
          sub={
            vel?.pct_change != null
              ? `${formatNumber(vel.recent_90d)} commits in 90d vs ${formatNumber(vel.prior_90d)} before`
              : `${formatNumber(vel?.recent_90d ?? 0)} commits in the last 90 days`
          }
          hint="The 90 days ending at the newest commit, against the 90 before it. Anchored to the latest commit rather than today, so a stale index doesn't read as a slowdown."
        />

        {rhythm.longest_streak && (
          <StatCallout
            label="Longest streak"
            value={`${formatNumber(rhythm.longest_streak.days)} days`}
            icon={<Flame className="h-4 w-4" />}
            tone="accent"
            sub={`${formatDate(rhythm.longest_streak.start)} – ${formatDate(
              rhythm.longest_streak.end,
            )}`}
          />
        )}

        {rhythm.busiest_day && (
          <StatCallout
            label="Busiest day"
            value={formatNumber(rhythm.busiest_day.commits)}
            icon={<CalendarDays className="h-4 w-4" />}
            sub={`commits on ${formatDate(rhythm.busiest_day.date)}`}
          />
        )}

        {rhythm.code_half_life_days != null && (
          <StatCallout
            label="Code half-life"
            value={`${formatNumber(rhythm.code_half_life_days)} days`}
            icon={<Snowflake className="h-4 w-4" />}
            tone="info"
            sub="half the files haven't been touched in longer than this"
            hint="Median time since each file was last changed, measured from the newest commit."
          />
        )}
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {rhythm.busiest_month && (
          <StatCallout
            label="Busiest month"
            value={monthLabel(rhythm.busiest_month.month)}
            sub={`${formatNumber(rhythm.busiest_month.total)} commits`}
          />
        )}
        <StatCallout
          label="Active days"
          value={formatNumber(rhythm.active_days)}
          sub="distinct days with at least one commit"
        />
      </div>
    </div>
  );
}
