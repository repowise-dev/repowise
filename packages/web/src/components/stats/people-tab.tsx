import { Users, UserCheck, AlertTriangle } from "lucide-react";
import type { StatsHighlights } from "@repowise-dev/types/stats";
import { StatCallout } from "@repowise-dev/ui/stats";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise-dev/ui/ui/card";
import { formatNumber } from "@repowise-dev/ui/lib/format";

export function PeopleTab({ data }: { data: StatsHighlights }) {
  const { people, activity } = data;
  const maxFiles = people.top_owners[0]?.file_count ?? 0;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCallout
          label="Contributors"
          value={formatNumber(activity.contributor_count)}
          icon={<Users className="h-4 w-4" />}
          sub="distinct commit authors"
        />
        <StatCallout
          label="File owners"
          value={formatNumber(people.owner_count)}
          icon={<UserCheck className="h-4 w-4" />}
          sub="hold primary ownership of ≥1 file"
        />
        <StatCallout
          label="Single-owner files"
          value={formatNumber(people.single_owner_files)}
          tone="warning"
          icon={<AlertTriangle className="h-4 w-4" />}
          sub="bus factor of 1 — knowledge risk"
        />
        <StatCallout
          label="Knowledge silos"
          value={formatNumber(people.silo_count)}
          tone="warning"
          sub="modules >80% owned by one person"
        />
      </div>

      {people.top_owners.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Top contributors by files owned</CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <ul className="space-y-2.5">
              {people.top_owners.map((o) => (
                <li key={o.name} className="space-y-1">
                  <div className="flex items-center justify-between text-sm">
                    <span className="truncate text-[var(--color-text-primary)]">{o.name}</span>
                    <span className="tabular-nums text-[var(--color-text-secondary)]">
                      {formatNumber(o.file_count)} files · {Math.round(o.pct * 100)}%
                    </span>
                  </div>
                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-bg-muted)]">
                    <div
                      className="h-full rounded-full bg-[var(--color-accent-primary)]"
                      style={{ width: `${maxFiles ? (o.file_count / maxFiles) * 100 : 0}%` }}
                    />
                  </div>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
