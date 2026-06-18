"use client";

/**
 * Knowledge Graph — `/repos/[id]/knowledge-graph`.
 *
 * The single curated mental-model surface: the layered architecture view
 * (tour, personas, layered C4). This route claims the "Knowledge Graph" name
 * so it stops leaking across the constellation, the layers error copy and the
 * legacy C4 header. No lens toggle — Communities and the dependency graphs live
 * under Architecture.
 *
 * Mounts the layered view client-side (it drives a Zustand store and ReactFlow).
 */

import { use } from "react";
import { Waypoints } from "lucide-react";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { KnowledgeGraphView } from "@/components/architecture/c4-view";
import { useRepo } from "@/lib/hooks/use-repo";

export default function KnowledgeGraphPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: repoId } = use(params);
  const { repo } = useRepo(repoId);
  const repoName = repo?.name ?? "System";

  return (
    <PageShell
      title="Knowledge Graph"
      icon={<Waypoints className="h-5 w-5 text-[var(--color-accent-primary)]" />}
      description="The curated layered view of the system — tour it, switch personas, drill into a layer."
      maxWidth="wide"
    >
      <div className="flex h-[calc(100vh-13rem)] min-h-[480px] flex-col overflow-hidden rounded-lg">
        <KnowledgeGraphView repoId={repoId} repoName={repoName} />
      </div>
    </PageShell>
  );
}
