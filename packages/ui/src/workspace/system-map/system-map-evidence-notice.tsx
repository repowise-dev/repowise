/**
 * One quiet line above the Live System Map when the graph it draws is short of
 * measured evidence: part of the estate is out of sight (`partial`), or too
 * little was linked for a score, cycle verdict or blast radius to mean anything
 * (`insufficient`). Names the reason from `core/workspace/graph_evidence.py`.
 *
 * A neutral note, never a health signal: it does not touch the health marks
 * on the nodes, and renders nothing when the evidence is measured or absent.
 */

import type { GraphEvidence } from "@repowise-dev/types";
import { Callout } from "../../shared/callout";

export interface SystemMapEvidenceNoticeProps {
  evidence: GraphEvidence | null | undefined;
  /** Word beside the dot for a partial graph. */
  partialLabel?: string;
  /** Word beside the dot for an insufficient graph. */
  insufficientLabel?: string;
}

export function SystemMapEvidenceNotice({
  evidence,
  partialLabel = "Partial evidence",
  insufficientLabel = "Not determined",
}: SystemMapEvidenceNoticeProps) {
  if (!evidence || evidence.state === "measured" || !evidence.summary) return null;
  const sentence = evidence.summary[0]!.toUpperCase() + evidence.summary.slice(1);
  return (
    <Callout label={evidence.state === "insufficient" ? insufficientLabel : partialLabel}>
      {sentence.endsWith(".") ? sentence : `${sentence}.`}
    </Callout>
  );
}
