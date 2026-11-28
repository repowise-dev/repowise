/**
 * The two prompt requests the Ownership views hand to the shared prompt
 * modal: a plan to spread knowledge one person holds, and a handover plan for
 * people leaving. The modal chrome is labelled; the prompt stays English.
 */

import type { AiPromptRequest } from "../health/ai-prompt-action";
import { buildHandoverPrompt, offboardingPromptAreas, riskPromptAreas } from "./handover-prompt";
import { joinNames } from "./knowledge-files";
import type { KnowledgeRiskArea } from "./knowledge-risks";
import type { OwnershipLabels } from "./labels";
import type { OffboardingArea, OffboardingResult } from "./offboarding";

const rootOrDir = (areas: readonly { dir: string }[], labels: OwnershipLabels) =>
  areas.length === 1 ? areas[0]!.dir || labels.repositoryRoot : null;

/** A plan for a second person to learn single-holder files, by area. */
export function spreadPromptRequest(
  areas: readonly KnowledgeRiskArea[],
  labels: OwnershipLabels,
  options: { spreadReason?: string; repoName?: string | undefined } = {},
): AiPromptRequest {
  const people = Array.from(new Set(areas.flatMap((a) => a.files.map((f) => f.holder.name))));
  const promptAreas = riskPromptAreas(areas);
  const files = areas.reduce((s, a) => s + a.fileCount, 0);
  return {
    title: labels.spreadTitle,
    description: labels.spreadDescription(files),
    filePath: rootOrDir(areas, labels),
    getPrompt: (flavor) =>
      buildHandoverPrompt({
        purpose: "spread",
        people,
        areas: promptAreas,
        flavor,
        ...(options.spreadReason ? { spreadReason: options.spreadReason } : {}),
        ...(options.repoName ? { repoName: options.repoName } : {}),
      }),
  };
}

/** A handover plan for some areas of an off-boarding result. */
export function handoverPromptRequest(
  result: OffboardingResult,
  areas: readonly OffboardingArea[],
  labels: OwnershipLabels,
  repoName?: string,
): AiPromptRequest {
  const people = result.leaving.map((p) => p.name);
  const promptAreas = offboardingPromptAreas(areas);
  const files = promptAreas.reduce((s, a) => s + a.files.length, 0);
  return {
    title: labels.handoverTitle,
    description: labels.handoverDescription(files, joinNames(people)),
    filePath: rootOrDir(areas, labels),
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
