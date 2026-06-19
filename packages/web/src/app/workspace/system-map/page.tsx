"use client";

import { useMemo } from "react";
import { useRouter } from "next/navigation";
import { Waypoints, Boxes, ArrowLeftRight, Share2 } from "lucide-react";
import { SystemMap, type RepoHealth } from "@repowise-dev/ui/workspace/system-map";
import { StatCard } from "@repowise-dev/ui/shared/stat-card";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import {
  useWorkspaceSystemGraph,
  useWorkspaceGraph,
} from "@/lib/hooks/use-workspace";

export default function SystemMapPage() {
  const router = useRouter();
  const { data: graph, isLoading, error } = useWorkspaceSystemGraph();
  const { data: repoGraph } = useWorkspaceGraph();

  // Join repo health (from the repo-level graph) onto service nodes by alias.
  // The map keys health by `SystemNode.repo`; the repo graph's node `name` is
  // the repo alias.
  const healthByRepo = useMemo<Map<string, RepoHealth>>(() => {
    const m = new Map<string, RepoHealth>();
    for (const n of repoGraph?.nodes ?? []) {
      m.set(n.name, { score: n.health_score, source: n.health_score_source });
    }
    return m;
  }, [repoGraph]);

  const diag = graph?.diagnostics;
  const serviceCount = graph?.nodes.length ?? 0;
  const edgeCount = graph?.edges.length ?? 0;

  return (
    <div className="p-5 sm:p-8 space-y-6 max-w-[1400px]">
      <div>
        <div className="flex items-center gap-2.5 mb-1">
          <Waypoints className="h-6 w-6 text-[var(--color-accent-primary)]" />
          <h1 className="text-2xl font-semibold text-[var(--color-text-primary)]">
            System Map
          </h1>
        </div>
        <p className="text-sm text-[var(--color-text-secondary)]">
          A live diagram of your services and the typed relationships between
          them, derived from code on every update.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          label="Services"
          value={isLoading ? "—" : serviceCount}
          icon={<Boxes className="h-4 w-4" />}
        />
        <StatCard
          label="Relationships"
          value={isLoading ? "—" : edgeCount}
          icon={<Share2 className="h-4 w-4 text-[var(--color-accent-secondary)]" />}
        />
        <StatCard
          label="Providers / Consumers"
          value={diag ? `${diag.total_providers} / ${diag.total_consumers}` : "—"}
          icon={<ArrowLeftRight className="h-4 w-4 text-[var(--color-success)]" />}
        />
        <StatCard
          label="Orphans / Weak links"
          value={diag ? `${diag.orphan_providers.length} / ${diag.weak_link_count}` : "—"}
          description="Providers with no consumer; low-confidence links"
          icon={<Share2 className="h-4 w-4 text-[var(--color-warning)]" />}
        />
      </div>

      <div className="rounded-lg overflow-hidden border border-[var(--color-border-default)] h-[calc(100vh-300px)] min-h-[560px]">
        {isLoading ? (
          <div className="p-4 h-full">
            <Skeleton className="h-full w-full" />
          </div>
        ) : (
          <SystemMap
            graph={graph}
            error={error ?? null}
            healthByRepo={healthByRepo}
            onOpenContract={() => router.push("/workspace/contracts")}
          />
        )}
      </div>
    </div>
  );
}
