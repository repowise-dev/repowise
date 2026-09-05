"use client";

import { GraphDocPanel as GraphDocPanelShell } from "@repowise-dev/ui/graph/graph-doc-panel";
import { fileEntityPath, filePageId } from "@repowise-dev/ui/shared/entity";
import { usePage } from "@/lib/hooks/use-page";
import type { DocPage } from "@repowise-dev/types/docs";

interface GraphDocPanelWrapperProps {
  repoId: string;
  nodeId: string;
  onClose: () => void;
}

export function GraphDocPanel({ repoId, nodeId, onClose }: GraphDocPanelWrapperProps) {
  const pageId = filePageId(nodeId);
  const { page, isLoading, error } = usePage(pageId, repoId);

  return (
    <GraphDocPanelShell
      nodeId={nodeId}
      page={page as DocPage | null | undefined}
      isLoading={isLoading}
      error={error}
      // Unconditional: the file page is a valid route whether or not a wiki
      // page was written for the file, and it is the empty state's way out.
      fullPageHref={fileEntityPath(`/repos/${repoId}`, nodeId)}
      browseDocsHref={`/repos/${repoId}/docs`}
      onClose={onClose}
    />
  );
}
