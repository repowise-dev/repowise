"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Waypoints, Boxes, ArrowLeftRight, Share2, Zap, AlertTriangle, ShieldAlert, Gauge } from "lucide-react";
import {
  SystemMap,
  SystemMapBlastPanel,
  SystemMapBreakingPanel,
  SystemMapConformancePanel,
  buildBlastRadiusOverlay,
  buildBreakingChangeOverlay,
  buildConformanceOverlay,
  buildArchitectureOverlay,
  type RepoHealth,
} from "@repowise-dev/ui/workspace/system-map";
import type { NodeArchitectureRole } from "@/lib/api/types";
import { StatCard } from "@repowise-dev/ui/shared/stat-card";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import {
  useWorkspaceSystemGraph,
  useWorkspaceGraph,
  useWorkspaceBlastRadius,
  useWorkspaceBreakingChanges,
  useWorkspaceConformance,
  useWorkspaceArchitecture,
} from "@/lib/hooks/use-workspace";

export default function SystemMapPage() {
  const router = useRouter();
  const { data: graph, isLoading, error } = useWorkspaceSystemGraph();
  const { data: repoGraph } = useWorkspaceGraph();

  // Blast-radius target. Driven by a page-level picker (and re-targetable from
  // the impacted panel) so the map component stays unchanged — the ripple rides
  // its existing `overlay` prop.
  const [blastTarget, setBlastTarget] = useState<string | null>(null);
  const [includeBehavioral, setIncludeBehavioral] = useState(true);
  const { data: blast, isLoading: blastLoading } = useWorkspaceBlastRadius(blastTarget, {
    includeBehavioral,
  });

  // Breaking-change guard. Lazy-fetched the first time it's toggled on; its
  // at-risk badges ride the map's overlay prop when no blast target is focused.
  const [showBreaking, setShowBreaking] = useState(false);
  const { data: breaking, isLoading: breakingLoading } =
    useWorkspaceBreakingChanges(showBreaking);

  // Architecture conformance. Lazy-fetched on toggle; violation/cycle badges ride
  // the same overlay prop when no blast target or breaking-change view is active.
  const [showConformance, setShowConformance] = useState(false);
  const { data: conformance, isLoading: conformanceLoading } =
    useWorkspaceConformance(showConformance);

  // Architecture metrics. Always fetched (cheap, deterministic): it feeds the
  // score stat and the per-service role shown in the inspector. The cyclic-core
  // highlight is an opt-in overlay toggle.
  const { data: architecture } = useWorkspaceArchitecture();
  const [showArchitecture, setShowArchitecture] = useState(false);

  const roleByNodeId = useMemo<Map<string, NodeArchitectureRole>>(() => {
    const m = new Map<string, NodeArchitectureRole>();
    for (const r of architecture?.roles ?? []) m.set(r.id, r);
    return m;
  }, [architecture]);

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

  // A blast-radius selection focuses the map (dim + ripple) and takes precedence;
  // otherwise the breaking-change overlay badges the at-risk seams additively.
  const overlay = useMemo(() => {
    if (!graph) return undefined;
    if (blast) return buildBlastRadiusOverlay(graph, blast);
    if (showConformance && conformance) return buildConformanceOverlay(graph, conformance);
    if (showBreaking && breaking) return buildBreakingChangeOverlay(graph, breaking);
    if (showArchitecture && architecture) return buildArchitectureOverlay(graph, architecture);
    return undefined;
  }, [graph, blast, showBreaking, breaking, showConformance, conformance, showArchitecture, architecture]);

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

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <StatCard
          label="Architecture score"
          value={architecture ? `${architecture.score.toFixed(1)} / 10` : "—"}
          description={
            architecture
              ? `${architecture.propagation_cost_pct.toFixed(1)}% propagation cost`
              : "Coupling + core roll-up"
          }
          icon={<Gauge className="h-4 w-4 text-[var(--color-accent-primary)]" />}
        />
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

      {/* Blast-radius controls: pick a service to see what breaks downstream. */}
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-4 py-2.5">
        <span className="inline-flex items-center gap-1.5 text-sm font-medium text-[var(--color-text-primary)]">
          <Zap className="h-4 w-4 text-[var(--color-accent-primary)]" />
          Blast radius
        </span>
        <select
          aria-label="Blast-radius source service"
          className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-canvas)] px-2 py-1 text-sm text-[var(--color-text-primary)]"
          value={blastTarget ?? ""}
          onChange={(e) => setBlastTarget(e.target.value || null)}
          disabled={!graph || serviceCount === 0}
        >
          <option value="">Select a service…</option>
          {(graph?.nodes ?? [])
            .slice()
            .sort((a, b) => a.id.localeCompare(b.id))
            .map((n) => (
              <option key={n.id} value={n.id}>
                {n.service_path ? `${n.repo} · ${n.name}` : n.name}
              </option>
            ))}
        </select>
        <label className="inline-flex items-center gap-1.5 text-sm text-[var(--color-text-secondary)]">
          <input
            type="checkbox"
            checked={includeBehavioral}
            onChange={(e) => setIncludeBehavioral(e.target.checked)}
          />
          Include co-change
        </label>
        <button
          type="button"
          onClick={() => {
            setShowArchitecture((v) => !v);
            if (!showArchitecture) {
              setShowBreaking(false);
              setShowConformance(false);
            }
          }}
          aria-pressed={showArchitecture}
          className={`ml-auto inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-sm ${
            showArchitecture
              ? "border-[var(--color-accent-primary)] text-[var(--color-accent-primary)]"
              : "border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
          }`}
          title="Highlight the cyclic architectural core"
        >
          <Gauge className="h-4 w-4" />
          Core
          {showArchitecture && architecture && architecture.core_size > 0 && (
            <span className="font-semibold">· {architecture.core_size}</span>
          )}
        </button>
        <button
          type="button"
          onClick={() => {
            setShowConformance((v) => !v);
            if (!showConformance) {
              setShowBreaking(false);
              setShowArchitecture(false);
            }
          }}
          aria-pressed={showConformance}
          className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-sm ${
            showConformance
              ? "border-[var(--color-risk-high)] text-[var(--color-risk-high)]"
              : "border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
          }`}
        >
          <ShieldAlert className="h-4 w-4" />
          Conformance
          {showConformance && conformance && conformance.violation_count > 0 && (
            <span className="font-semibold">· {conformance.violation_count}</span>
          )}
        </button>
        <button
          type="button"
          onClick={() => {
            setShowBreaking((v) => !v);
            if (!showBreaking) {
              setShowConformance(false);
              setShowArchitecture(false);
            }
          }}
          aria-pressed={showBreaking}
          className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-sm ${
            showBreaking
              ? "border-[var(--color-risk-high)] text-[var(--color-risk-high)]"
              : "border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
          }`}
        >
          <AlertTriangle className="h-4 w-4" />
          Breaking changes
          {showBreaking && breaking && breaking.breaking_count > 0 && (
            <span className="font-semibold">· {breaking.breaking_count}</span>
          )}
        </button>
        {blastTarget && (
          <button
            type="button"
            onClick={() => setBlastTarget(null)}
            className="text-sm text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
          >
            Clear
          </button>
        )}
      </div>

      <div className="relative rounded-lg overflow-hidden border border-[var(--color-border-default)] h-[calc(100vh-360px)] min-h-[560px]">
        {isLoading ? (
          <div className="p-4 h-full">
            <Skeleton className="h-full w-full" />
          </div>
        ) : (
          <>
            <SystemMap
              graph={graph}
              error={error ?? null}
              healthByRepo={healthByRepo}
              {...(overlay ? { overlay } : {})}
              roleByNodeId={roleByNodeId}
              onOpenContract={() => router.push("/workspace/contracts")}
            />
            <SystemMapBlastPanel
              result={blast}
              loading={blastLoading}
              onSelectTarget={setBlastTarget}
              onClear={() => setBlastTarget(null)}
            />
            {showBreaking && (
              <SystemMapBreakingPanel
                report={breaking}
                loading={breakingLoading}
                onSelectNode={setBlastTarget}
                onClear={() => setShowBreaking(false)}
              />
            )}
            {showConformance && (
              <SystemMapConformancePanel
                report={conformance}
                loading={conformanceLoading}
                onSelectNode={setBlastTarget}
                onClear={() => setShowConformance(false)}
              />
            )}
          </>
        )}
      </div>
    </div>
  );
}
