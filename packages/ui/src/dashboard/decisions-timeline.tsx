import { Landmark, Lightbulb } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { Badge } from "../ui/badge";
import { EmptyState } from "../shared/empty-state";
import { formatRelativeTime, stripMarkdown } from "../lib/format";

/** Structural slice of a decision record — anything with these fields renders
 *  (full DecisionRecord rows and the overview-summary slim slice both fit). */
export interface DecisionsTimelineItem {
  id: string;
  title: string;
  status: string;
  created_at: string | null;
  source?: string | null;
}

interface DecisionsTimelineProps {
  decisions: DecisionsTimelineItem[];
  repoId: string;
  linkPrefix?: string;
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

export function DecisionsTimeline({ decisions, repoId, linkPrefix }: DecisionsTimelineProps) {
  const prefix = linkPrefix ?? `/repos/${repoId}`;
  const recent = decisions.slice(0, 6);

  if (recent.length === 0) {
    return (
      <Card>
        <CardContent className="p-0">
          <EmptyState
            icon={<Lightbulb className="h-8 w-8" />}
            title="No decisions recorded"
            description="No architectural decisions have been detected in this repository."
          />
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
            href={`${prefix}/decisions`}
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
                href={`${prefix}/decisions/${d.id}`}
                className="flex items-start gap-3 pl-0 group relative"
              >
                <span
                  className={`h-[11px] w-[11px] rounded-full shrink-0 mt-0.5 ring-2 ring-[var(--color-bg-surface)] relative z-10 ${STATUS_COLORS[d.status] ?? STATUS_COLORS["active"]}`}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium text-[var(--color-text-primary)] truncate group-hover:text-[var(--color-accent-primary)] transition-colors">
                      {stripMarkdown(d.title)}
                    </span>
                    <Badge
                      variant="outline"
                      className={`text-[10px] h-4 shrink-0 ${STATUS_BADGE_VARIANT[d.status] ?? ""}`}
                    >
                      {d.status}
                    </Badge>
                  </div>
                  <span className="text-[10px] text-[var(--color-text-tertiary)]">
                    {d.created_at ? formatRelativeTime(d.created_at) : ""}
                    {d.source && ` · ${d.source.replace("_", " ")}`}
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
