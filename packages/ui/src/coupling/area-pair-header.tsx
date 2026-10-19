"use client";

import { ChevronLeft } from "lucide-react";
import type { CouplingAreaPair } from "@repowise-dev/types/coupling";
import { AiPromptButton } from "../health/ai-prompt-button";
import type { AreaCouplingCopy } from "./area-copy";
import { ALL_FILE_PAIRS, areaLabel, type AreaPairRef } from "./area-view-state";

export interface AreaPairHeaderProps {
  selection: AreaPairRef | typeof ALL_FILE_PAIRS;
  /** The open pair's row, when the list holds it; absent past the list's cap. */
  opened?: CouplingAreaPair | undefined;
  copy: AreaCouplingCopy;
  onBack: () => void;
  onPrompt: (pairs: CouplingAreaPair[]) => void;
}

/** The drill-down's header: back to the list, the pair named, its headline and prompt. */
export function AreaPairHeader({ selection, opened, copy, onBack, onPrompt }: AreaPairHeaderProps) {
  const title =
    selection === ALL_FILE_PAIRS
      ? copy.allFilePairs
      : `${areaLabel(selection.source, copy.rootLabel)} ↔ ${areaLabel(selection.target, copy.rootLabel)}`;
  return (
    <>
      <nav aria-label={copy.backToAreas} className="flex flex-wrap items-center gap-2 text-xs">
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center gap-1 text-[var(--color-accent-primary)] hover:underline"
        >
          <ChevronLeft className="h-3.5 w-3.5" aria-hidden />
          {copy.backToAreas}
        </button>
      </nav>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <h2 className="break-all font-mono text-[18px] text-[var(--color-text-primary)]">{title}</h2>
          {opened && (
            <p className="text-xs text-[var(--color-text-secondary)]">
              {copy.pairHeadline(opened.unexplained_pairs, opened.pairs)}
            </p>
          )}
        </div>
        {opened?.hidden && (
          <AiPromptButton label={copy.promptLabel} onClick={() => onPrompt([opened])} />
        )}
      </div>
    </>
  );
}
