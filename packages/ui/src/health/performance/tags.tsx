"use client";

import type { PerformanceOpportunity } from "@repowise-dev/types/health";

import { LowerPriorityTag } from "../lower-priority-tag";
import { PreviewBadge } from "../preview-badge";

/** The tags a cause can carry, on its row and in its drawer: why it can wait,
 *  and a language cell served as a preview. */
export function OpportunityTags({ opportunity }: { opportunity: PerformanceOpportunity }) {
  return (
    <>
      {opportunity.lower_priority ? (
        <p className="mt-1">
          <LowerPriorityTag reason={opportunity.lower_priority} />
        </p>
      ) : null}
      {opportunity.preview ? (
        <p className="mt-1">
          <PreviewBadge note={opportunity.preview} />
        </p>
      ) : null}
    </>
  );
}
