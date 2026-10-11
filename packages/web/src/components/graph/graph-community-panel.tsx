"use client";

import { GraphCommunityPanel as GraphCommunityPanelShell } from "@repowise-dev/ui/graph/graph-community-panel";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import { useCommunityDetail } from "@/lib/hooks/use-graph";
import type { CommunityDetail, GraphPopulation } from "@repowise-dev/types/graph";

interface GraphCommunityPanelWrapperProps {
  repoId: string;
  communityId: number;
  population?: GraphPopulation | undefined;
  onClose: () => void;
  onEnterCommunity?: (() => void) | undefined;
  onNeighborSelect?: ((communityId: number) => void) | undefined;
}

export function GraphCommunityPanel({
  repoId,
  communityId,
  population,
  onClose,
  onEnterCommunity,
  onNeighborSelect,
}: GraphCommunityPanelWrapperProps) {
  const { community, isLoading } = useCommunityDetail(repoId, communityId, population);

  return (
    <GraphCommunityPanelShell
      communityId={communityId}
      community={community as CommunityDetail | null | undefined}
      isLoading={isLoading}
      onClose={onClose}
      onEnterCommunity={onEnterCommunity}
      onNeighborSelect={onNeighborSelect}
      memberHref={(path) => fileEntityPath(`/repos/${repoId}`, path)}
      // Code Health's triage map takes `?file=` and opens on that row, so a hot
      // member gets an exact destination rather than a repo-wide page.
      healthHrefFor={(path) =>
        `/repos/${repoId}/code-health?tab=triage&file=${encodeURIComponent(path)}`
      }
      deadCodeHref={`/repos/${repoId}/code-health?tab=dead-code`}
      codeHealthHref={`/repos/${repoId}/code-health`}
    />
  );
}
