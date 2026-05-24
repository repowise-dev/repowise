"use client";

import { useRouter } from "next/navigation";
import {
  Sidebar,
  useArchitectureStore,
  type ArchNodeHealth,
} from "@repowise-dev/ui/c4";
import { ChatMarkdown } from "@repowise-dev/ui/chat/chat-markdown";
import { useC4SelectionContext, useC4DocsPathSet } from "@/lib/hooks/use-c4-context";

interface ArchDetailPanelHostProps {
  repoId: string;
}

export function ArchDetailPanelHost({ repoId }: ArchDetailPanelHostProps) {
  const router = useRouter();
  const selectedNodeId = useArchitectureStore((s) => s.selectedNodeId);
  const nodesById = useArchitectureStore((s) => s.nodesById);

  const node = selectedNodeId ? nodesById.get(selectedNodeId) ?? null : null;
  const filePath = node?.file_path ?? null;

  const { pageIdByPath } = useC4DocsPathSet(repoId);
  const pageId = (() => {
    if (!filePath) return null;
    const direct = pageIdByPath.get(filePath);
    if (direct) return direct;
    let parent = filePath;
    while (parent.includes("/")) {
      parent = parent.substring(0, parent.lastIndexOf("/"));
      const hit = pageIdByPath.get(parent);
      if (hit) return hit;
    }
    return null;
  })();

  const { health, page, isLoading } = useC4SelectionContext(
    repoId,
    filePath,
    pageId,
  );

  const contributors =
    health?.owners
      ?.slice(0, 5)
      .map((o) => ({ name: o.name, files: o.file_count, pct: o.pct })) ?? [];

  const archHealth: ArchNodeHealth | undefined = health
    ? {
        health_score: health.health_score,
        hotspot_count: health.hotspot_count,
        dead_code_count: health.dead_code_count,
        doc_coverage_pct: health.doc_coverage_pct,
        contributor_count: health.contributor_count,
        is_silo: health.is_silo,
      }
    : undefined;

  return (
    <Sidebar
      health={archHealth}
      contributors={contributors}
      docContent={page?.content ?? null}
      renderDoc={(content) => <ChatMarkdown content={content} />}
      onOpenInGraph={
        filePath
          ? (p) => router.push(`/repos/${repoId}/graph?node=${encodeURIComponent(p)}`)
          : undefined
      }
      onOpenDoc={() => {
        if (page) {
          router.push(`/repos/${repoId}/docs/${encodeURIComponent(page.id)}`);
        }
      }}
    />
  );
}
