"use client";

import { GraphCommunityPanel as GraphCommunityPanelShell } from "@repowise-dev/ui/graph/graph-community-panel";
import { useCommunityDetail } from "@/lib/hooks/use-graph";
import type { CommunityDetail } from "@repowise-dev/types/graph";

interface GraphCommunityPanelWrapperProps {
  repoId: string;
  communityId: number;
  onClose: () => void;
  onExpandOnCanvas?: (() => void) | undefined;
}

export function GraphCommunityPanel({
  repoId,
  communityId,
  onClose,
  onExpandOnCanvas,
}: GraphCommunityPanelWrapperProps) {
  const { community, isLoading } = useCommunityDetail(repoId, communityId);

  return (
    <GraphCommunityPanelShell
      communityId={communityId}
      community={community as CommunityDetail | null | undefined}
      isLoading={isLoading}
      onClose={onClose}
      onExpandOnCanvas={onExpandOnCanvas}
    />
  );
}
