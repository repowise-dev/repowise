import Link from "next/link";
import { Flame } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise/ui/ui/card";
import type { HotspotResponse } from "@/lib/api/types";
import { truncatePath } from "@repowise/ui/lib/format";

interface HotspotsMiniProps {
  hotspots: HotspotResponse[];
  repoId: string;
}

export function HotspotsMini({ hotspots, repoId }: HotspotsMiniProps) {
  const top = hotspots.slice(0, 5);

  if (top.length === 0) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <Flame className="h-4 w-4 text-red-500" />
            Top Hotspots
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-0">
          <p className="text-xs text-[var(--color-text-tertiary)]">No hotspot data</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center justify-between">
          <span className="flex items-center gap-2">
            <Flame className="h-4 w-4 text-red-500" />
            Top Hotspots
          </span>
          <Link
            href={`/repos/${repoId}/hotspots`}
            className="text-[10px] text-[var(--color-accent-primary)] hover:underline font-normal"
          >
            View all
          </Link>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="space-y-2">
          {top.map((h) => (
            <div key={h.file_path} className="flex items-center gap-3">
              {/* Churn bar */}
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
              {/* File info */}
              <div className="min-w-0 flex-1">
                <p className="text-[11px] font-mono text-[var(--color-text-primary)] truncate">
                  {truncatePath(h.file_path, 40)}
                </p>
              </div>
              {/* Stats */}
              <div className="flex items-center gap-2 shrink-0 text-[10px] text-[var(--color-text-tertiary)] tabular-nums">
                <span>{h.commit_count_90d}c/90d</span>
                <span className="text-[var(--color-text-secondary)]">
                  {Math.round(h.churn_percentile)}%
                </span>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function getChurnColor(percentile: number): string {
  if (percentile >= 95) return "var(--color-error)";
  if (percentile >= 80) return "var(--color-warning)";
  return "var(--color-accent-primary)";
}
