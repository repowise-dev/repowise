import type { StatsHighlights } from "@repowise-dev/types/stats";
import { ActivityTrendChart, StatCallout } from "@repowise-dev/ui/stats";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise-dev/ui/ui/card";
import { formatNumber, formatDate } from "@repowise-dev/ui/lib/format";

function monthLabel(month: string): string {
  // "2026-06" → "Jun 2026"
  const [y, m] = month.split("-");
  const d = new Date(Number(y), Number(m) - 1, 1);
  return new Intl.DateTimeFormat("en-US", { month: "short", year: "numeric" }).format(d);
}

export function GrowthTab({ data }: { data: StatsHighlights }) {
  const { activity } = data;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCallout
          label="Total commits"
          value={formatNumber(activity.total_commits)}
          sub={
            activity.first_commit_at && activity.last_commit_at
              ? `${formatDate(activity.first_commit_at)} → ${formatDate(activity.last_commit_at)}`
              : "across the indexed window"
          }
        />
        <StatCallout
          label="Busiest month"
          value={activity.busiest_month ? monthLabel(activity.busiest_month.month) : "—"}
          sub={
            activity.busiest_month
              ? `${formatNumber(activity.busiest_month.total)} commits`
              : "no monthly data"
          }
        />
        <StatCallout
          label="Fix commits"
          value={`${activity.fix_pct}%`}
          tone="warning"
          sub={`${formatNumber(activity.fix_commits)} commits classified as fixes`}
        />
        <StatCallout
          label="Agent commits"
          value={`${activity.agent_pct}%`}
          tone="info"
          sub={`${formatNumber(activity.agent_commits)} agent-authored`}
        />
      </div>

      <ActivityTrendChart monthly={activity.monthly} />

      {activity.agent_names.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Agents at work</CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <ul className="divide-y divide-[var(--color-border-subtle,var(--color-border-default))]">
              {activity.agent_names.map((a) => (
                <li key={a.name} className="flex items-center justify-between py-2 text-sm">
                  <span className="truncate text-[var(--color-text-primary)]">{a.name}</span>
                  <span className="tabular-nums text-[var(--color-text-secondary)]">
                    {formatNumber(a.count)} commits
                  </span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
