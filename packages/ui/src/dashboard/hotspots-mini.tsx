import { Flame } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { EmptyState } from "../shared/empty-state";
import { truncatePath } from "../lib/format";
import { fileEntityPath } from "../shared/entity/routes";
import type { Hotspot } from "@repowise-dev/types/git";

interface HotspotsMiniProps {
  hotspots: Hotspot[];
  repoId: string;
  linkPrefix?: string;
  /**
   * Total hotspot count (from the paginated envelope). When provided the
   * header shows "Top 5 of N" so the user understands the mini card is a
   * preview, not the full set. Without this, the "View all" link is the
   * only signal that more exists.
   */
  total?: number;
  /** Preview window — defaults to 5; cap at 10 for a glance card. */
  previewCount?: number;
}

function getChurnColor(percentile: number): string {
  if (percentile >= 95) return "var(--color-error)";
  if (percentile >= 80) return "var(--color-warning)";
  return "var(--color-accent-primary)";
}

export function HotspotsMini({
  hotspots,
  repoId,
  linkPrefix,
  total,
  previewCount = 5,
}: HotspotsMiniProps) {
  const prefix = linkPrefix ?? `/repos/${repoId}`;
  const top = hotspots.slice(0, Math.min(previewCount, 10));
  // Authoritative total is the server-reported figure when present; fall
  // back to the array length so this widget still works when callers
  // haven't migrated to the paginated fetch yet.
  const fullCount = total ?? hotspots.length;

  if (top.length === 0) {
    return (
      <Card>
        <CardContent className="p-0">
          <EmptyState
            icon={<Flame className="h-8 w-8" />}
            title="No hotspot data"
            description="No churn or complexity hotspots detected in this repository."
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
            <Flame className="h-4 w-4 text-[var(--color-error)]" />
            Top Hotspots
            <span className="text-[10px] font-normal text-[var(--color-text-tertiary)] tabular-nums">
              {top.length} of {fullCount.toLocaleString()}
            </span>
          </span>
          <a
            href={`${prefix}/code-health?tab=hotspots`}
            className="text-[10px] text-[var(--color-accent-primary)] hover:underline font-normal"
          >
            View all
          </a>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="space-y-2">
          {top.map((h) => (
            <a
              key={h.file_path}
              href={fileEntityPath(prefix, h.file_path)}
              className="flex items-center gap-3 -mx-2 px-2 py-0.5 rounded hover:bg-[var(--color-bg-elevated)] transition-colors"
            >
              <div className="w-16 shrink-0">
                <div className="h-1.5 rounded-full bg-[var(--color-bg-elevated)] overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all"
                    style={{
                      width: `${h.churn_percentile}%`,
                      backgroundColor: getChurnColor(h.churn_percentile),
                    }}
                  />
                </div>
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-xs font-mono text-[var(--color-text-primary)] truncate">
                  {truncatePath(h.file_path, 40)}
                </p>
              </div>
              <div className="flex items-center gap-2 shrink-0 text-[10px] text-[var(--color-text-tertiary)] tabular-nums">
                <span>{h.commit_count_90d}c/90d</span>
                <span className="text-[var(--color-text-secondary)]">
                  {Math.round(h.churn_percentile)}%
                </span>
              </div>
            </a>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
