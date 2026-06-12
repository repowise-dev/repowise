"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Network, Loader2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise-dev/ui/ui/card";
import { WorkspaceGraph, type WorkspaceGraphData } from "@repowise-dev/ui/workspace/workspace-graph";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { getWorkspaceGraph } from "@/lib/api/workspace";

interface WorkspaceGraphSectionProps {
  repoCount: number;
}

export function WorkspaceGraphSection({ repoCount }: WorkspaceGraphSectionProps) {
  const router = useRouter();
  const [data, setData] = useState<WorkspaceGraphData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (repoCount < 2) {
      setLoading(false);
      return;
    }
    getWorkspaceGraph()
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [repoCount]);

  if (repoCount < 2) {
    return (
      <section>
        <h2 className="text-sm font-medium text-[var(--color-text-primary)] mb-3">
          Cross-Repo Graph
        </h2>
        <EmptyState
          icon={<Network className="h-8 w-8" />}
          title="Not enough repositories"
          description="Add at least 2 repositories to see cross-repo relationships."
        />
      </section>
    );
  }

  return (
    <section>
      <h2 className="text-sm font-medium text-[var(--color-text-primary)] mb-3">
        Cross-Repo Graph
      </h2>
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <Network className="h-4 w-4 text-[var(--color-accent-primary)]" />
            Workspace Overview
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-0">
          <div className="h-[450px] rounded-lg overflow-hidden border border-[var(--color-border-default)]">
            {loading ? (
              <div className="flex items-center justify-center h-full">
                <Loader2 className="w-5 h-5 animate-spin text-[var(--color-text-tertiary)]" />
              </div>
            ) : data && data.nodes.length >= 2 ? (
              <WorkspaceGraph
                data={data}
                onRepoClick={(repoId) => router.push(`/repos/${repoId}/architecture?view=graph`)}
              />
            ) : (
              <div className="flex items-center justify-center h-full">
                <EmptyState
                  icon={<Network className="h-8 w-8" />}
                  title="No graph data"
                  description="Repository data is still being indexed."
                />
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </section>
  );
}
