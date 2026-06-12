"use client";

/**
 * Web-side wrapper around <C4DetailPanel> that fetches per-selection
 * docs + module health and feeds them into the shared UI panel.
 *
 * The shared panel lives in @repowise-dev/ui/c4 — no fetch logic. This
 * host knits SWR data into the panel's prop shape.
 */

import { useRouter } from "next/navigation";
import { C4DetailPanel, type C4NodeData } from "@repowise-dev/ui/c4";
import { ChatMarkdown } from "@repowise-dev/ui/chat/chat-markdown";
import {
  useC4SelectionContext,
} from "@/lib/hooks/use-c4-context";

interface C4DetailPanelHostProps {
  repoId: string;
  data: C4NodeData;
  pageIdByPath: ReadonlyMap<string, string>;
  onClose: () => void;
  onDrillIn?: ((containerId: string) => void) | undefined;
}

function selectionPath(data: C4NodeData): string | null {
  if (data.kind === "container") return data.container.path;
  if (data.kind === "component") return data.component.path;
  return null;
}

export function C4DetailPanelHost({
  repoId,
  data,
  pageIdByPath,
  onClose,
  onDrillIn,
}: C4DetailPanelHostProps) {
  const router = useRouter();
  const path = selectionPath(data);
  const pageId = path ? pageIdByPath.get(path) ?? null : null;

  const { health, page, isLoading } = useC4SelectionContext(repoId, path, pageId);

  const doc = page
    ? {
        title: page.title,
        excerpt: page.content.split("\n").slice(0, 6).join("\n"),
        href: `/repos/${repoId}/docs?page=${encodeURIComponent(page.id)}`,
      }
    : null;

  const contributors =
    health?.owners?.slice(0, 5).map((o) => ({ name: o.name, files: o.file_count, pct: o.pct })) ?? [];

  return (
    <C4DetailPanel
      data={data}
      loading={isLoading}
      doc={doc}
      docContent={page ? (page.content.length > 300 ? page.content.slice(0, 300).trimEnd() + "..." : page.content) : null}
      renderDoc={(content) => <ChatMarkdown content={content} />}
      health={
        health
          ? {
              health_score: health.health_score,
              hotspot_count: health.hotspot_count,
              dead_code_count: health.dead_code_count,
              doc_coverage_pct: health.doc_coverage_pct,
              primary_owner: health.primary_owner,
              primary_owner_pct: health.primary_owner_pct,
              contributor_count: health.contributor_count,
              is_silo: health.is_silo,
            }
          : null
      }
      contributors={contributors}
      onClose={onClose}
      onDrillIn={onDrillIn}
      onOpenInGraph={path ? (p) => router.push(`/repos/${repoId}/architecture?view=graph&node=${encodeURIComponent(p)}`) : undefined}
      onOpenDoc={(href) => router.push(href)}
    />
  );
}
