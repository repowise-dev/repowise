"use client";

import { PathFinderPanel as PathFinderPanelShell } from "@repowise-dev/ui/graph/path-finder-panel";
import { getGraphPath, searchNodes } from "@/lib/api/graph";

interface PathFinderPanelWrapperProps {
  repoId: string;
  onPathFound: (pathNodes: string[]) => void;
  onClear: () => void;
  onClose: () => void;
  initialFrom?: string;
  initialTo?: string;
}

export function PathFinderPanel({
  repoId,
  onPathFound,
  onClear,
  onClose,
  initialFrom = "",
  initialTo = "",
}: PathFinderPanelWrapperProps) {
  return (
    <PathFinderPanelShell
      searchNodes={(query, limit) => searchNodes(repoId, query, limit)}
      findPath={(from, to) => getGraphPath(repoId, from, to)}
      onPathFound={onPathFound}
      onClear={onClear}
      onClose={onClose}
      initialFrom={initialFrom}
      initialTo={initialTo}
    />
  );
}
