"use client";

import { GraphDocPanel as GraphDocPanelShell } from "@repowise-dev/ui/graph/graph-doc-panel";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import { usePage } from "@/lib/hooks/use-page";
import type { DocPage } from "@repowise-dev/types/docs";

interface GraphDocPanelWrapperProps {
  repoId: string;
  nodeId: string;
  onClose: () => void;
}

export function GraphDocPanel({ repoId, nodeId, onClose }: GraphDocPanelWrapperProps) {
  const pageId = `file_page:${nodeId}`;
  const { page, isLoading, error } = usePage(pageId);

  return (
    <GraphDocPanelShell
      nodeId={nodeId}
      page={page as DocPage | null | undefined}
      isLoading={isLoading}
      error={error}
      fullPageHref={page ? fileEntityPath(`/repos/${repoId}`, nodeId) : undefined}
      browseDocsHref={`/repos/${repoId}/docs`}
      onClose={onClose}
    />
  );
}
