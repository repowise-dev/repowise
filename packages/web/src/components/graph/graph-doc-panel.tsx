"use client";

import { GraphDocPanel as GraphDocPanelShell } from "@repowise-dev/ui/graph/graph-doc-panel";
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
      fullPageHref={
        page ? `/repos/${repoId}/wiki/${encodeURIComponent(page.id)}` : undefined
      }
      browseDocsHref={`/repos/${repoId}/docs`}
      onClose={onClose}
    />
  );
}
