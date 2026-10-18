"use client";

import * as React from "react";
import { AiPromptButton } from "../health/ai-prompt-button";
import { AGENT_PROMPT_LABEL, useAiPromptAction, type AiPromptRequest } from "../health/ai-prompt-action";
import { CleanState } from "../shared/clean-state";
import { StatRibbon } from "../stats/stat-ribbon";
import { buildHandoverPrompt, offboardingPromptAreas } from "./handover-prompt";
import { KnowledgeAreaList, KnowledgeAreaRow } from "./knowledge-area-list";
import { joinNames } from "./knowledge-files";
import type { OffboardingArea, OffboardingResult } from "./offboarding";

/**
 * How an off-boarding result reads, wherever it is shown: the what-if on the
 * Ownership page, a quiet owner on Knowledge loss, and "What depends on
 * them" on a person page. Summary first, then directories, then files.
 */

const pct = (share: number) => `${Math.round(share * 100)}%`;

/** A share of the codebase; a sliver reads "under 1%", never "0%". */
const codeShare = (share: number) => (share > 0 && share < 0.005 ? "under 1%" : pct(share));

/** The handover prompt request for some areas of a result. */
export function handoverPromptRequest(
  result: OffboardingResult,
  areas: readonly OffboardingArea[],
  repoName?: string,
): AiPromptRequest {
  const people = result.leaving.map((p) => p.name);
  const promptAreas = offboardingPromptAreas(areas);
  const files = promptAreas.reduce((s, a) => s + a.files.length, 0);
  return {
    title: "Plan the handover",
    description: `A handover plan for ${files.toLocaleString()} ${files === 1 ? "file" : "files"} that depend on ${joinNames(people)}.`,
    filePath: areas.length === 1 ? areas[0]!.dir || "(repository root)" : null,
    getPrompt: (flavor) =>
      buildHandoverPrompt({
        purpose: "handover",
        people,
        areas: promptAreas,
        flavor,
        threshold: result.threshold,
        ...(repoName ? { repoName } : {}),
      }),
  };
}

function areaSummary(area: OffboardingArea): string {
  const heir = area.inheritor
    ? `${area.inheritor.person.name} could take over: the strongest remaining author on ${area.inheritor.files.toLocaleString()} of ${area.fileCount.toLocaleString()}`
    : "Nobody who stays is among these files' main authors";
  const severe =
    area.severe > 0
      ? `. ${area.severe.toLocaleString()} ${area.severe === 1 ? "is a hotspot or in" : "are hotspots or in"} weak health`
      : "";
  return `${heir}${severe}.`;
}

export interface OffboardingAreasProps {
  areas: readonly OffboardingArea[];
  onPrompt: (area: OffboardingArea) => void;
  base?: string | undefined;
  LinkComponent?: React.ElementType | undefined;
  initial?: number;
}

export function OffboardingAreas({ areas, onPrompt, base, LinkComponent, initial }: OffboardingAreasProps) {
  return (
    <KnowledgeAreaList
      label="Directories that would lose their majority author"
      areas={areas}
      keyOf={(a) => a.dir}
      {...(initial ? { initial } : {})}
      renderArea={(area) => (
        <KnowledgeAreaRow
          dir={area.dir}
          summary={areaSummary(area)}
          files={area.files.map((f) => ({
            ...f,
            note: f.inheritor ? `${f.inheritor.name} ${pct(f.inheritor.share)}` : "no other author",
          }))}
          onPrompt={() => onPrompt(area)}
          base={base}
          LinkComponent={LinkComponent}
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
}

export function OffboardingResultView({
  result,
  base,
  repoName,
  LinkComponent,
  secondaryAction,
  initialAreas,
}: OffboardingResultViewProps) {
  const { openPrompt, promptModal } = useAiPromptAction();
  const { totals } = result;
  const names = joinNames(result.leaving.map((p) => p.name));
  const threshold = pct(result.threshold);
  const promptArea = React.useCallback(
    (area: OffboardingArea) => openPrompt(handoverPromptRequest(result, [area], repoName)),
    [openPrompt, result, repoName],
  );

  if (result.leaving.length === 0) {
    return (
      <p className="text-[15px] leading-relaxed text-[var(--color-text-secondary)]">
        None of the selected people appear in the indexed history. They may have committed under a
        different address; search for them by name.
      </p>
    );
  }

  if (totals.atRiskFiles === 0) {
    return (
      <CleanState
        align="start"
        title={
          totals.touchedFiles > 0
            ? `Nothing depends on ${names} alone`
            : `${names} ${result.leaving.length === 1 ? "holds" : "hold"} no share of the indexed code`
        }
        description={
          totals.touchedFiles > 0
            ? `The people who stay hold at least ${threshold} of the commits on all ${totals.touchedFiles.toLocaleString()} files ${names} worked on.`
            : "No code file lists them among its top five authors."
        }
      >
        {secondaryAction}
      </CleanState>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-3">
        <p className="max-w-[68ch] text-[15px] leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
          Without {names},{" "}
          <strong className="font-semibold text-[var(--color-text-primary)]">
            {totals.atRiskFiles.toLocaleString()} {totals.atRiskFiles === 1 ? "file" : "files"}
          </strong>{" "}
          would be left with authors holding under {threshold} of the commits:{" "}
          {codeShare(totals.nlocShare)} of the code
          {totals.severeFiles > 0
            ? `, ${totals.severeFiles.toLocaleString()} of them hotspots or in weak health`
            : ""}
          . {(totals.touchedFiles - totals.atRiskFiles).toLocaleString()} more carry some of their
          history but keep at least {threshold} with the people who stay.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <AiPromptButton
            onClick={() => openPrompt(handoverPromptRequest(result, result.areas, repoName))}
            label={AGENT_PROMPT_LABEL}
          />
          {secondaryAction}
        </div>
      </div>
      <StatRibbon
        stats={[
          { label: "Files at risk", value: totals.atRiskFiles.toLocaleString() },
          {
            label: "Hotspots among them",
            value: totals.hotspots.toLocaleString(),
            hint: "At-risk files in the repository's high-churn band",
          },
          {
            label: "Share of code",
            value: codeShare(totals.nlocShare),
            hint: `Lines of code in at-risk files, of ${totals.totalNloc.toLocaleString()} across ${totals.trackedFiles.toLocaleString()} files`,
          },
          { label: "Directories", value: result.areas.length.toLocaleString() },
        ]}
      />
      <OffboardingAreas
        areas={result.areas}
        onPrompt={promptArea}
        base={base}
        LinkComponent={LinkComponent}
        {...(initialAreas ? { initial: initialAreas } : {})}
      />
      {promptModal}
    </div>
  );
}
