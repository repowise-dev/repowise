"use client";

import type { PerformanceOpportunity } from "@repowise-dev/types/health";

import { AdaptivePanel } from "../../../shared/adaptive-panel";
import { AGENT_PROMPT_LABEL } from "../../ai-prompt-action";
import { AiPromptButton } from "../../ai-prompt-button";
import type { PerformanceViewAdapter } from "../adapter";
import { useOpportunityDetail } from "../cache";
import { RawObservations } from "../evidence";
import { opportunityTitle } from "../presentation";
import {
  CodeSection,
  FixSummary,
  MapLink,
  RelatedSection,
  SiblingsNote,
  WhereToEdit,
} from "./fix";
import {
  AgentHandoffSection,
  CallerPathsSection,
  ChangeabilitySection,
  EvidenceSection,
  TestsSection,
  WhyRankedSection,
} from "./judgements";
import { StatusNotices } from "./layout";
import { PlanSection, useVerifiedPlan } from "./plan";

/**
 * One opportunity in depth.
 *
 * The three judgements the analysis makes separately are shown separately:
 * how well the evidence resolved, whether reducing the work is likely valid,
 * and whether one named transformation is safe. Collapsing them would let
 * strong evidence read as a safe fix.
 *
 * Mounted only while a cause is open, and loaded on first open, so the queue
 * never pays for it.
 */
export default function OpportunityDrawer({
  opportunity,
  adapter,
  detailEnabled,
  planEnabled,
  onClose,
  onAgentPrompt,
  onSelectById,
}: {
  /** The row that was inspected, held so the panel can render before the fetch. */
  opportunity: PerformanceOpportunity;
  adapter: PerformanceViewAdapter;
  detailEnabled: boolean;
  planEnabled: boolean;
  onClose: () => void;
  onAgentPrompt: (opportunity: PerformanceOpportunity) => void;
  /** Open a sibling cause by id, replacing the drawer's contents in place. */
  onSelectById?: ((opportunityId: string) => void) | undefined;
}) {
  const { data: detail } = useOpportunityDetail(
    adapter,
    detailEnabled ? opportunity.opportunity_id : null,
  );

  // The row is the fallback body: a host without the detail call still reads
  // every field the queue carried, and simply learns nothing extra.
  const resolved = detail && detail.found ? detail : null;
  const current: PerformanceOpportunity = resolved ?? opportunity;
  const plan = useVerifiedPlan(current, adapter, planEnabled);

  return (
    <AdaptivePanel
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      eyebrow="Performance fix"
      title={opportunityTitle(current)}
      widthClassName="md:max-w-[680px]"
    >
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex-1 space-y-7 overflow-y-auto px-5 py-5">
          <WhereToEdit opportunity={current} />
          <StatusNotices detail={detail} />
          <MapLink adapter={adapter} opportunity={current} />
          <FixSummary opportunity={current} />
          <CodeSection opportunity={current} adapter={adapter} />
          {current.siblings && current.siblings.length > 0 ? (
            <SiblingsNote siblings={current.siblings} onSelect={onSelectById} />
          ) : null}
          <ChangeabilitySection current={current} />
          <PlanSection opportunity={current} adapter={adapter} enabled={planEnabled} plan={plan} />
          <RelatedSection adapter={adapter} filePath={current.file_path} />
          <TestsSection current={current} adapter={adapter} />
          <EvidenceSection current={current} />
          {/* The row's place in the queue: a lookup by id has no list. */}
          <WhyRankedSection current={current} position={opportunity.rank_position} />
          <CallerPathsSection current={current} adapter={adapter} />
          <RawObservations
            opportunityId={current.opportunity_id}
            preview={current.evidence}
            previewTruncated={current.evidence_truncated}
            total={current.observations_total}
            adapter={adapter}
          />
          <AgentHandoffSection current={current} resolved={resolved} />
        </div>

        <div className="flex items-center gap-3 border-t border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-5 py-3.5">
          <AiPromptButton
            label={AGENT_PROMPT_LABEL}
            onClick={() => onAgentPrompt(current)}
          />
        </div>
      </div>
    </AdaptivePanel>
  );
}
