"use client";

import { useQueryState } from "nuqs";
import type { CommitResponse, CommitStats } from "@/lib/api/types";
import { CommitRiskHistogram } from "@repowise-dev/ui/commits/commit-risk-histogram";
import { CommitRiskScatter } from "@repowise-dev/ui/commits/commit-risk-scatter";

/**
 * The two views of how the score behaves here.
 *
 * They stay a pair. The histogram says where the tercile cuts fall, the
 * scatter says what commit shape lands you above them, and neither answers the
 * question alone — showing one leaves a half-width chart beside dead space,
 * which is the empty state the old collapsible produced whenever a repo
 * predated the histogram aggregate.
 *
 * A client island only because both charts hover, and because clicking a dot
 * writes `?commit=` for the detail sheet to pick up.
 */
export function CommitDistribution({
  stats,
  recent,
}: {
  stats: CommitStats | null;
  recent: CommitResponse[];
}) {
  const [, setSelectedSha] = useQueryState("commit");

  const hasHistogram = (stats?.risk_histogram?.length ?? 0) > 0;
  if (!hasHistogram || !stats || recent.length === 0) return null;

  return (
    <div className="grid grid-cols-1 gap-7 lg:grid-cols-2 lg:gap-10">
      <div className="flex flex-col gap-2">
        <h3 className="text-[13px] font-semibold text-[var(--color-text-primary)]">
          Score distribution
        </h3>
        <p className="max-w-[62ch] text-xs leading-relaxed text-[var(--color-text-tertiary)]">
          Every scored commit, binned on the raw 0 to 10 score rather than the
          percentile. Percentile ranks are uniform by construction, so that axis
          has no shape to draw. The dashed lines are the tercile cuts behind each
          row&apos;s priority pill.
        </p>
        <CommitRiskHistogram stats={stats} />
      </div>
      <div className="flex flex-col gap-2">
        <h3 className="text-[13px] font-semibold text-[var(--color-text-primary)]">
          Size against diffusion
        </h3>
        <p className="max-w-[62ch] text-xs leading-relaxed text-[var(--color-text-tertiary)]">
          The {recent.length.toLocaleString()} most recent commits, on their own
          recency sample rather than the feed above: that defaults to risk-sorted,
          so reusing it would plot only the top tercile and call it the spread.
          Big and scattered is what the model penalises. Click a dot to open it.
        </p>
        <CommitRiskScatter
          commits={recent}
          onSelect={(sha) => void setSelectedSha(sha)}
        />
      </div>
    </div>
  );
}
