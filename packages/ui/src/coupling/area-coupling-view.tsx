"use client";

import { useCallback, useMemo, type ReactNode } from "react";
import type { CouplingAreaPair, CouplingAreasResponse } from "@repowise-dev/types/coupling";
import { useAiPromptAction } from "../health/ai-prompt-action";
import { buildAreaCouplingAiPrompt } from "../health/ai-prompt-builder";
import { resolveAreaCouplingCopy, type AreaCouplingCopyOverrides } from "./area-copy";
import { CouplingAreaFiltersBar } from "./area-filters";
import { AreaPairHeader } from "./area-pair-header";
import { CouplingAreaPairList } from "./area-pair-list";
import {
  ALL_FILE_PAIRS,
  areaLabel,
  type AreaPairRef,
  type CouplingAreaFilters,
} from "./area-view-state";

type LinkLike = React.ElementType<{ href: string; className?: string; children: ReactNode }>;

export interface CouplingAreaViewProps {
  data: CouplingAreasResponse;
  filters: CouplingAreaFilters;
  onFiltersChange: (next: CouplingAreaFilters) => void;
  /** The open area pair, `"all"` for every file pair, or `null` for the area list. */
  selection: AreaPairRef | typeof ALL_FILE_PAIRS | null;
  onSelectionChange: (next: AreaPairRef | typeof ALL_FILE_PAIRS | null) => void;
  /** The file-pair explorer for the open selection, fetched and rendered by the host. */
  children?: ReactNode;
  repoName?: string;
  /** Cross-repo co-changes, when the repository is part of a workspace. */
  workspaceHref?: string;
  LinkComponent?: LinkLike;
  copy?: AreaCouplingCopyOverrides;
}

/** The list row for the open pair, when the list holds it. */
function openedRow(
  data: CouplingAreasResponse,
  selection: AreaPairRef | typeof ALL_FILE_PAIRS | null,
): CouplingAreaPair | undefined {
  if (!selection || selection === ALL_FILE_PAIRS) return undefined;
  return (data.area_pairs ?? []).find(
    (p) => p.source === selection.source && p.target === selection.target,
  );
}

const LINK = "text-xs text-[var(--color-accent-primary)] hover:underline";

/**
 * Change coupling, opened at area level: which directories change together
 * with no dependency between their files. A selected area pair drills into
 * its file pairs, drawn by the existing explorer the host passes as children.
 */
export function CouplingAreaView({
  data,
  filters,
  onFiltersChange,
  selection,
  onSelectionChange,
  children,
  repoName,
  workspaceHref,
  LinkComponent = "a",
  copy: overrides,
}: CouplingAreaViewProps) {
  const copy = useMemo(() => resolveAreaCouplingCopy(overrides), [overrides]);
  const { openPrompt, promptModal } = useAiPromptAction();

  const prompt = useCallback(
    (pairs: CouplingAreaPair[]) =>
      openPrompt({
        title: copy.promptTitle,
        description: copy.promptDescription,
        filePath: pairs
          .map((p) => `${areaLabel(p.source, copy.rootLabel)} ↔ ${areaLabel(p.target, copy.rootLabel)}`)
          .join(", "),
        getPrompt: (flavor) =>
          buildAreaCouplingAiPrompt({ areaPairs: pairs, flavor, ...(repoName ? { repoName } : {}) }),
      }),
    [openPrompt, copy, repoName],
  );

  const open = useCallback((pair: AreaPairRef) => onSelectionChange(pair), [onSelectionChange]);

  const opened = openedRow(data, selection);

  const footer = (
    <span className="flex flex-wrap items-center gap-4">
      <button type="button" className={LINK} onClick={() => onSelectionChange(ALL_FILE_PAIRS)}>
        {copy.browseFilePairs}
      </button>
      {workspaceHref && (
        <LinkComponent href={workspaceHref} className={LINK}>
          {copy.workspaceLink}
        </LinkComponent>
      )}
    </span>
  );

  return (
    <div className="space-y-5">
      <CouplingAreaFiltersBar
        value={filters}
        onChange={onFiltersChange}
        resolvedDepth={data.depth}
        copy={copy.filters}
      />

      {selection === null ? (
        <CouplingAreaPairList
          data={data}
          copy={copy}
          testsIncluded={filters.includeTests}
          onOpen={open}
          onPrompt={prompt}
          footer={footer}
        />
      ) : (
        <div className="space-y-4">
          <AreaPairHeader
            selection={selection}
            opened={opened}
            copy={copy}
            onBack={() => onSelectionChange(null)}
            onPrompt={prompt}
          />
          {children}
        </div>
      )}
      {promptModal}
    </div>
  );
}
