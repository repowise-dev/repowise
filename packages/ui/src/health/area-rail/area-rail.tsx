"use client";

/**
 * The map's context rail: what the selection is, one level at a time.
 * Nothing selected reads the repository; an area reads that area; a file
 * shows the file's full health detail, with the way back to its area.
 */

import type { ReactNode } from "react";
import useSWR from "swr";
import type { AreaDetail } from "@repowise-dev/types/areas";

import { ApiError } from "../../shared/api-error";
import { toFriendlyMessage } from "../../lib/errors";
import { Skeleton, SkeletonRegion } from "../../ui/skeleton";
import { useAiPromptAction } from "../ai-prompt-action";
import type { AreaIndex } from "../map/areas";
import { AreaCard, type AreaLinks } from "./area-card";
import type { AreaRailLabels } from "./labels";

export interface AreaRailProps {
  cacheKey: string;
  getAreaDetail: (path: string) => Promise<AreaDetail>;
  area: string;
  index: AreaIndex;
  nameOf: (path: string) => string;
  labels: AreaRailLabels;
  selectedFile: string | null;
  /** The file's detail, rendered by the host, shown while a file is selected. */
  fileDetail: ReactNode;
  onClearFile: () => void;
  onSelectArea: (path: string) => void;
  onSelectFile: (path: string) => void;
  areaLinks?: ((path: string) => AreaLinks) | undefined;
  onNavigate?: ((href: string) => void) | undefined;
  /** What the figures cover ("Production code"), when the map holds files out. */
  scope?: string | undefined;
  /** Said under a load error: the folder may hold only files the map leaves out. */
  errorHint?: string | undefined;
  /** Part of the cache key: a card read under one hold is not another's. */
  holdKey?: string | undefined;
}

function CardSkeleton({ label }: { label: string }) {
  return (
    <SkeletonRegion className="flex flex-col gap-4" label={label}>
      <Skeleton className="h-10 w-2/3" />
      <Skeleton className="h-8 w-1/2" />
      <Skeleton className="h-2 w-full" />
      <Skeleton className="h-7 w-48" />
      <Skeleton className="h-24 w-full" />
    </SkeletonRegion>
  );
}

export function AreaRail(props: AreaRailProps) {
  const { cacheKey, getAreaDetail, area, labels } = props;
  const { openPrompt, promptModal } = useAiPromptAction();
  const { data, error, mutate, isLoading } = useSWR<AreaDetail>(
    props.selectedFile ? null : `area-detail:${cacheKey}:${props.holdKey ?? ""}:${area}`,
    () => getAreaDetail(area),
    { revalidateOnFocus: false, keepPreviousData: true },
  );

  if (props.selectedFile) {
    return (
      <div className="flex flex-col gap-3">
        <button
          type="button"
          onClick={props.onClearFile}
          className="self-start rounded font-mono text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
        >
          ← {labels.backToArea(area ? props.nameOf(area) : labels.repository)}
        </button>
        {props.fileDetail}
      </div>
    );
  }
  if (error && !data) {
    return (
      <ApiError
        size="compact"
        title={labels.loadError}
        message={props.errorHint ?? toFriendlyMessage(error)}
        onRetry={() => void mutate()}
      />
    );
  }
  if (!data || isLoading) return <CardSkeleton label={labels.loading} />;
  return (
    <>
      <AreaCard
        detail={data}
        index={props.index}
        nameOf={props.nameOf}
        labels={labels}
        onSelectArea={props.onSelectArea}
        onSelectFile={props.onSelectFile}
        openPrompt={openPrompt}
        links={props.areaLinks?.(area)}
        onNavigate={props.onNavigate}
        scope={props.scope}
      />
      {promptModal}
    </>
  );
}
