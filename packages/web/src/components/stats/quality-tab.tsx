import type { StatsHighlights } from "@repowise-dev/types/stats";
import { StatCallout } from "@repowise-dev/ui/stats";
import { LanguageDonut } from "@repowise-dev/ui/dashboard/language-donut";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise-dev/ui/ui/card";
import { formatNumber, truncatePath } from "@repowise-dev/ui/lib/format";

const BAND_COLOR: Record<string, string> = {
  healthy: "var(--color-success)",
  warning: "var(--color-warning)",
  alert: "var(--color-error)",
};

function score(v: number | null): string {
  return v != null ? `${v.toFixed(1)}/10` : "—";
}

export function QualityTab({ data }: { data: StatsHighlights }) {
  const { quality, scale } = data;
  const dist = quality.distribution;

  const langDistribution: Record<string, number> = {};
  for (const l of scale.languages) langDistribution[l.language] = l.file_count;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCallout
          label="Defect risk"
          value={score(quality.average_health)}
          tone="accent"
          sub="bug-predictive headline score"
        />
        <StatCallout
          label="Maintainability"
          value={score(quality.maintainability_average)}
          sub="cohesion, brain methods, DRY"
        />
        <StatCallout
          label="Performance"
          value={score(quality.performance_average)}
          sub="I/O-in-loop / N+1 risk"
        />
        <StatCallout
          label="Test coverage"
          value={quality.test_coverage_pct != null ? `${quality.test_coverage_pct}%` : "—"}
          sub="files with a matching test"
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {dist && (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Health distribution</CardTitle>
            </CardHeader>
            <CardContent className="pt-0">
              <div className="flex h-3 w-full overflow-hidden rounded-full">
                {(["healthy", "warning", "alert"] as const).map((b) =>
                  dist.bands[b].pct > 0 ? (
                    <div
                      key={b}
                      style={{ width: `${dist.bands[b].pct}%`, background: BAND_COLOR[b] }}
                      title={`${b}: ${dist.bands[b].pct}%`}
                    />
                  ) : null,
                )}
              </div>
              <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
                {(["healthy", "warning", "alert"] as const).map((b) => (
                  <div key={b} className="flex items-center gap-1.5">
                    <span
                      className="h-2 w-2 rounded-full"
                      style={{ background: BAND_COLOR[b] }}
                    />
                    <span className="capitalize text-[var(--color-text-secondary)]">{b}</span>
                    <span className="tabular-nums text-[var(--color-text-tertiary)]">
                      {dist.bands[b].files}
                    </span>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        )}

        <LanguageDonut distribution={langDistribution} />
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCallout
          label="Open findings"
          value={formatNumber(quality.open_findings)}
          sub={`${quality.severity_breakdown.critical} critical · ${quality.severity_breakdown.high} high`}
        />
        <StatCallout
          label="Dead code"
          value={formatNumber(quality.dead_code.deletable_lines)}
          tone="warning"
          sub={`recoverable lines · ${formatNumber(quality.dead_code.total_findings)} findings`}
        />
        <StatCallout
          label="Doc coverage"
          value={`${Math.round(quality.doc_coverage_pct)}%`}
          sub={`${formatNumber(quality.page_count)} wiki pages`}
        />
        <StatCallout
          label="Lowest-scoring file"
          value={quality.worst_performer_score != null ? score(quality.worst_performer_score) : "—"}
          tone="warning"
          sub={
            quality.worst_performer_path
              ? truncatePath(quality.worst_performer_path, 30)
              : "no data"
          }
        />
      </div>
    </div>
  );
}
