"use client";

import * as React from "react";
import { AiPromptButton } from "../health/ai-prompt-button";
import { useAiPromptAction } from "../health/ai-prompt-action";
import { CleanState } from "../shared/clean-state";
import { StatRibbon } from "../stats/stat-ribbon";
import { KnowledgeAreaList, KnowledgeAreaRow } from "./knowledge-area-list";
import { joinNames } from "./knowledge-files";
import { ownershipLabels, type OwnershipLabels } from "./labels";
import type { OffboardingArea, OffboardingResult } from "./offboarding";
import { handoverPromptRequest } from "./prompt-requests";

/**
 * How an off-boarding result reads, wherever it is shown: the what-if on the
 * Ownership page, a quiet owner on Knowledge loss, and "What depends on
 * them" on a person page. Summary first, then directories, then files.
 */

const pct = (share: number) => `${Math.round(share * 100)}%`;

/** A share of the codebase; a sliver reads "under 1%", never "0%". */
const codeShare = (share: number, labels: OwnershipLabels) =>
  share > 0 && share < 0.005 ? labels.underOnePercent : pct(share);

function areaSummary(area: OffboardingArea, labels: OwnershipLabels): string {
  const heir = area.inheritor
    ? labels.heir(area.inheritor.person.name, area.inheritor.files, area.fileCount)
    : labels.noHeir;
  return area.severe > 0 ? `${heir}. ${labels.heirSevere(area.severe)}.` : `${heir}.`;
}

export interface OffboardingAreasProps {
  areas: readonly OffboardingArea[];
  onPrompt: (area: OffboardingArea) => void;
  labels: OwnershipLabels;
  base?: string | undefined;
  LinkComponent?: React.ElementType | undefined;
  initial?: number;
}

export function OffboardingAreas({ areas, onPrompt, labels, base, LinkComponent, initial }: OffboardingAreasProps) {
  return (
    <KnowledgeAreaList
      label={labels.offboardingListLabel}
      showAllLabel={labels.showAllDirectories}
      areas={areas}
      keyOf={(a) => a.dir}
      {...(initial ? { initial } : {})}
      renderArea={(area) => (
        <KnowledgeAreaRow
          dir={area.dir}
          summary={areaSummary(area, labels)}
          files={area.files.map((f) => ({
            ...f,
            note: f.inheritor ? `${f.inheritor.name} ${pct(f.inheritor.share)}` : labels.noOtherAuthor,
          }))}
          onPrompt={() => onPrompt(area)}
          base={base}
          LinkComponent={LinkComponent}
          labels={labels}
        />
      )}
    />
  );
}

export interface OffboardingResultViewProps {
  result: OffboardingResult;
  base?: string | undefined;
  repoName?: string | undefined;
  LinkComponent?: React.ElementType | undefined;
  /** Beside the primary prompt, e.g. "Open in off-boarding". */
  secondaryAction?: React.ReactNode;
  /** Directories shown before "Show all". */
  initialAreas?: number;
  labels?: Partial<OwnershipLabels> | undefined;
}

/** Nothing at risk: either nothing depends on them, or they hold no share at all. */
function NothingAtRisk({
  result,
  names,
  labels,
  children,
}: {
  result: OffboardingResult;
  names: string;
  labels: OwnershipLabels;
  children?: React.ReactNode;
}) {
  const touched = result.totals.touchedFiles;
  return (
    <CleanState
      align="start"
      title={touched > 0 ? labels.nothingDependsTitle(names) : labels.noShareTitle(names, result.leaving.length)}
      description={
        touched > 0 ? labels.nothingDependsDescription(pct(result.threshold), touched, names) : labels.noShareDescription
      }
    >
      {children}
    </CleanState>
  );
}

export function OffboardingResultView({
  result,
  base,
  repoName,
  LinkComponent,
  secondaryAction,
  initialAreas,
  labels: overrides,
}: OffboardingResultViewProps) {
  const labels = React.useMemo(() => ownershipLabels(overrides), [overrides]);
  const { openPrompt, promptModal } = useAiPromptAction();
  const { totals } = result;
  const names = joinNames(result.leaving.map((p) => p.name));
  const promptArea = React.useCallback(
    (area: OffboardingArea) => openPrompt(handoverPromptRequest(result, [area], labels, repoName)),
    [openPrompt, result, labels, repoName],
  );

  if (result.leaving.length === 0) {
    return <p className="text-[15px] leading-relaxed text-[var(--color-text-secondary)]">{labels.unknownPeople}</p>;
  }
  if (totals.atRiskFiles === 0) {
    return (
      <NothingAtRisk result={result} names={names} labels={labels}>
        {secondaryAction}
      </NothingAtRisk>
    );
  }

  const share = codeShare(totals.nlocShare, labels);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-3">
        <p className="max-w-[68ch] text-[15px] leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
          {labels.resultSentence({
            names,
            files: totals.atRiskFiles,
            threshold: pct(result.threshold),
            codeShare: share,
            severe: totals.severeFiles,
            kept: totals.touchedFiles - totals.atRiskFiles,
          })}
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <AiPromptButton
            onClick={() => openPrompt(handoverPromptRequest(result, result.areas, labels, repoName))}
            label={labels.agentPrompt}
          />
          {secondaryAction}
        </div>
      </div>
      <StatRibbon
        stats={[
          { label: labels.statAtRisk, value: totals.atRiskFiles.toLocaleString() },
          { label: labels.statHotspots, value: totals.hotspots.toLocaleString(), hint: labels.statHotspotsHint },
          { label: labels.statShare, value: share, hint: labels.statShareHint(totals.totalNloc, totals.trackedFiles) },
          { label: labels.statDirectories, value: result.areas.length.toLocaleString() },
        ]}
      />
      <OffboardingAreas
        areas={result.areas}
        onPrompt={promptArea}
        labels={labels}
        base={base}
        LinkComponent={LinkComponent}
        {...(initialAreas ? { initial: initialAreas } : {})}
      />
      {promptModal}
    </div>
  );
}
