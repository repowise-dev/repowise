import { Landmark } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { Badge } from "../ui/badge";
import { formatRelativeTime } from "../lib/format";
import type { DecisionRecord } from "@repowise-dev/types/decisions";

interface DecisionsTimelineProps {
  decisions: DecisionRecord[];
  repoId: string;
}

const STATUS_COLORS: Record<string, string> = {
  active: "bg-[var(--color-success)]",
  proposed: "bg-[var(--color-info)]",
  deprecated: "bg-[var(--color-error)]",
  superseded: "bg-[var(--color-text-tertiary)]",
};

const STATUS_BADGE_VARIANT: Record<string, string> = {
  active: "text-[var(--color-success)] border-[var(--color-success)]/30",
  proposed: "text-[var(--color-info)] border-[var(--color-info)]/30",
  deprecated: "text-[var(--color-error)] border-[var(--color-error)]/30",
  superseded: "text-[var(--color-text-tertiary)] border-[var(--color-text-tertiary)]/30",
};

export function DecisionsTimeline({ decisions, repoId }: DecisionsTimelineProps) {
  const recent = decisions.slice(0, 6);

  if (recent.length === 0) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <Landmark className="h-4 w-4 text-[var(--color-text-secondary)]" />
            Recent Decisions
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-0">
          <p className="text-xs text-[var(--color-text-tertiary)]">No decisions recorded</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center justify-between">
          <span className="flex items-center gap-2">
            <Landmark className="h-4 w-4 text-[var(--color-text-secondary)]" />
            Recent Decisions
          </span>
          <a
            href={`/repos/${repoId}/decisions`}
            className="text-[10px] text-[var(--color-accent-primary)] hover:underline font-normal"
          >
            View all
          </a>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="relative">
          <div className="absolute left-[5px] top-1 bottom-1 w-px bg-[var(--color-border-default)]" />

          <div className="space-y-3">
            {recent.map((d) => (
              <a
                key={d.id}
                href={`/repos/${repoId}/decisions/${d.id}`}
                className="flex items-start gap-3 pl-0 group relative"
              >
                <span
                  className={`h-[11px] w-[11px] rounded-full shrink-0 mt-0.5 ring-2 ring-[var(--color-bg-surface)] relative z-10 ${STATUS_COLORS[d.status] ?? STATUS_COLORS["active"]}`}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium text-[var(--color-text-primary)] truncate group-hover:text-[var(--color-accent-primary)] transition-colors">
                      {d.title}
                    </span>
                    <Badge
                      variant="outline"
                      className={`text-[9px] h-4 shrink-0 ${STATUS_BADGE_VARIANT[d.status] ?? ""}`}
                    >
                      {d.status}
                    </Badge>
                  </div>
                  <span className="text-[10px] text-[var(--color-text-tertiary)]">
                    {formatRelativeTime(d.created_at)}
                    {d.source && ` Â· ${d.source.replace("_", " ")}`}
                  </span>
                </div>
              </a>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
