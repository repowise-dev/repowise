"use client";

import * as React from "react";
import type { KnowledgeMatrix } from "@repowise-dev/types/owners";
import { AiPromptButton } from "../health/ai-prompt-button";
import { useAiPromptAction } from "../health/ai-prompt-action";
import { CleanState } from "../shared/clean-state";
import { KnowledgeAreaList, KnowledgeAreaRow } from "./knowledge-area-list";
import { knowledgeRisks, type KnowledgeRiskArea } from "./knowledge-risks";
import { ownershipLabels, type OwnershipLabels } from "./labels";
import { spreadPromptRequest } from "./prompt-requests";

export interface KnowledgeRisksViewProps {
  matrix: KnowledgeMatrix;
  base?: string | undefined;
  repoName?: string | undefined;
  LinkComponent?: React.ElementType | undefined;
  labels?: Partial<OwnershipLabels> | undefined;
}

const pct = (share: number) => `${Math.round(share * 100)}%`;

function areaSummary(area: KnowledgeRiskArea, labels: OwnershipLabels): string {
  const who =
    area.holders === 1 && area.holder
      ? labels.risksOneHolder(area.holder.person.name)
      : labels.risksManyHolders(area.holders, area.holder?.person.name ?? null, area.holder?.files ?? 0);
  return area.severe > 0 ? `${who}. ${labels.risksSevere(area.severe)}.` : `${who}.`;
}

/**
 * Where one person holds code that matters now: single-author files that are
 * hotspots or in weak health, by directory. The action is a plan to spread
 * that knowledge, handed to the reader's agent.
 */
export function KnowledgeRisksView({ matrix, base, repoName, LinkComponent, labels: overrides }: KnowledgeRisksViewProps) {
  const labels = React.useMemo(() => ownershipLabels(overrides), [overrides]);
  const risks = React.useMemo(() => knowledgeRisks(matrix), [matrix]);
  const { openPrompt, promptModal } = useAiPromptAction();
  // One area from its row, or every area (ranked, the prompt lists the first few) from the section.
  const promptFor = React.useCallback(
    (areas: readonly KnowledgeRiskArea[]) => openPrompt(spreadPromptRequest(areas, labels, { repoName })),
    [openPrompt, labels, repoName],
  );

  if (risks.files === 0) {
    return (
      <CleanState
        align="start"
        title={labels.risksCleanTitle(risks.trackedFiles)}
        description={labels.risksCleanDescription}
      />
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="max-w-[68ch] text-[15px] leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
        {labels.risksIntro(risks.files, risks.hotspots, risks.lowHealth, risks.areas.length)}
      </p>
      <AiPromptButton className="self-start" onClick={() => promptFor(risks.areas)} label={labels.agentPrompt} />
      <KnowledgeAreaList
        label={labels.risksListLabel}
        showAllLabel={labels.showAllDirectories}
        areas={risks.areas}
        keyOf={(a) => a.dir}
        renderArea={(area) => (
          <KnowledgeAreaRow
            dir={area.dir}
            summary={areaSummary(area, labels)}
            files={area.files.map((f) => ({ ...f, note: `${f.holder.name} ${pct(f.holder.share)}` }))}
            onPrompt={() => promptFor([area])}
            base={base}
            LinkComponent={LinkComponent}
            labels={labels}
          />
        )}
      />
      {promptModal}
    </div>
  );
}
