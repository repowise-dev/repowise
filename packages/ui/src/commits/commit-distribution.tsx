import * as React from "react";
import type { Commit, CommitStats } from "@repowise-dev/types/git";
import { CommitRiskHistogram } from "./commit-risk-histogram";
import { CommitRiskScatter } from "./commit-risk-scatter";

export interface CommitDistributionProps {
  stats: CommitStats | null;
  /** A recency sample, not the risk-sorted feed — see the note below. */
  recent: Commit[];
  /** Opening one commit. The host decides what that means. */
  onSelect?: ((sha: string) => void) | undefined;
}

/**
 * Two views of how the supporting diff-shape score behaves in this repo.
 *
 * They stay a pair. The histogram says where the tercile cuts fall, the
 * scatter says what commit shape lands you above them, and neither answers the
 * question alone — showing one leaves a half-width chart beside dead space,
 * which is the empty state the old collapsible produced whenever a repo
 * predated the histogram aggregate.
 *
 * This lives in the shared package rather than in the web app because it is
 * mostly *content*: two headings and two paragraphs explaining a scoring model
 * that behaves identically wherever it runs. Left in the app, a second surface
 * showing the same charts had to copy the prose, and a copied explanation of a
 * model is an explanation that drifts out of step with it.
 *
 * Portability: it takes data and one callback. The host owns how a selected
 * commit is expressed — the OSS web app writes `?commit=` for its detail sheet
 * to pick up — so there is no platform flag here and no dead branch.
 */
export function CommitDistribution({ stats, recent, onSelect }: CommitDistributionProps) {
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
          Big and scattered is what the model penalises.
          {onSelect ? " Click a dot to open it." : ""}
        </p>
        <CommitRiskScatter commits={recent} onSelect={onSelect} />
      </div>
    </div>
  );
}
