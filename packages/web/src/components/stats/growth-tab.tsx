import type { StatsHighlights } from "@repowise-dev/types/stats";
import { ActivityTrendChart, StatCallout, AGENT_PCT_HINT } from "@repowise-dev/ui/stats";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise-dev/ui/ui/card";
import { formatNumber, formatDate } from "@repowise-dev/ui/lib/format";

function monthLabel(month: string): string {
  // "2026-06" → "Jun 2026"
  const [y, m] = month.split("-");
  const d = new Date(Number(y), Number(m) - 1, 1);
  return new Intl.DateTimeFormat("en-US", { month: "short", year: "numeric" }).format(d);
}

const RISK_BANDS = [
  { key: "low", label: "Low", color: "var(--color-success)" },
  { key: "moderate", label: "Moderate", color: "var(--color-warning)" },
  { key: "high", label: "High", color: "var(--color-error)" },
] as const;

export function GrowthTab({ data }: { data: StatsHighlights }) {
  const { activity } = data;
  const risk = activity.change_risk_mix;
  const riskTotal = risk.low + risk.moderate + risk.high;

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
          sub={`${formatNumber(activity.agent_commits)} commits with verifiable agent signatures`}
          hint={AGENT_PCT_HINT}
        />
      </div>

      {riskTotal > 0 && (
        <Card>
          <CardHeader className="flex-row items-center justify-between gap-3 pb-3">
            <CardTitle className="text-sm">Change-risk mix</CardTitle>
            <span className="text-[11px] text-[var(--color-text-tertiary)]">
              calibrated per-commit risk across {formatNumber(riskTotal)} commits
            </span>
          </CardHeader>
          <CardContent className="pt-0">
            <div className="flex h-3 w-full overflow-hidden rounded-full">
              {RISK_BANDS.map((b) => {
                const pct = (risk[b.key] / riskTotal) * 100;
                return pct > 0 ? (
                  <div
                    key={b.key}
                    style={{ width: `${pct}%`, background: b.color }}
                    title={`${b.label}: ${risk[b.key]} (${Math.round(pct)}%)`}
                  />
                ) : null;
              })}
            </div>
            <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
              {RISK_BANDS.map((b) => (
                <div key={b.key} className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full" style={{ background: b.color }} />
                  <span className="text-[var(--color-text-secondary)]">{b.label}</span>
                  <span className="tabular-nums text-[var(--color-text-tertiary)]">
                    {formatNumber(risk[b.key])}
                  </span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

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
