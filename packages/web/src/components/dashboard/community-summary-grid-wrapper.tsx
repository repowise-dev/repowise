"use client";

import { useState } from "react";
import { CommunitySummaryGrid as CommunitySummaryGridShell } from "@repowise-dev/ui/dashboard/community-summary-grid";
import { useCommunityDetail } from "@/lib/hooks/use-graph";
import type { CommunityDetail, CommunitySummaryItem } from "@repowise-dev/types/graph";

interface Props {
  communities: CommunitySummaryItem[];
  repoId: string;
}

/**
 * Lifts `useCommunityDetail` into the wrapper. The original component
 * spawned the hook inside an inner `CommunityDetail` subcomponent which
 * was rendered (and unmounted) per-row; pulling the active id up here
 * keeps a single SWR subscription per click and lets the shell stay
 * presentational.
 */
export function CommunitySummaryGridWrapper({ communities, repoId }: Props) {
  const [activeId, setActiveId] = useState<number | null>(null);
  const { community, isLoading } = useCommunityDetail(repoId, activeId);

  const details: Record<number, CommunityDetail | null> =
    activeId != null && community ? { [activeId]: community } : {};

  return (
    <CommunitySummaryGridShell
      communities={communities}
      details={details}
      loadingDetailId={isLoading ? activeId : null}
      onExpand={(id) => setActiveId(id)}
    />
  );
}
