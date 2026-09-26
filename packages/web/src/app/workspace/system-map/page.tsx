"use client";

import { useCallback, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Waypoints } from "lucide-react";
import {
  SystemMap,
  SYSTEM_MAP_CANVAS_HEIGHT,
  SystemMapFindings,
  SystemMapLensControl,
  SystemMapLensResults,
  buildArchitectureOverlay,
  buildBlastRadiusOverlay,
  buildBreakingChangeOverlay,
  buildConformanceOverlay,
  plural,
  selectionRepo,
  type ContractRef,
  type RepoHealth,
  type SegmentOption,
  type SystemMapLens,
  type SystemMapSelection,
} from "@repowise-dev/ui/workspace/system-map";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { PageLede } from "@repowise-dev/ui/shared/page-lede";
import { StatRibbon, type RibbonStat } from "@repowise-dev/ui/stats/stat-ribbon";
import { OverviewSection } from "@repowise-dev/ui/overview/section";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import type { NodeArchitectureRole, SystemGraph, ConformanceReport } from "@/lib/api/types";
import {
  useWorkspaceSystemGraph,
  useWorkspaceGraph,
  useWorkspaceBlastRadius,
  useWorkspaceBreakingChanges,
  useWorkspaceConformance,
  useWorkspaceArchitecture,
} from "@/lib/hooks/use-workspace";
import { useRepoContracts } from "./use-repo-contracts";

const LENSES: readonly SystemMapLens[] = ["none", "blast", "breaking", "conformance", "core"];

function contractHref(ref: ContractRef): string {
  const params = new URLSearchParams({ contract: ref.contract_id, repo: ref.repo });
  if (ref.file_path) params.set("file", ref.file_path);
  return `/workspace/contracts?${params.toString()}`;
}

function contractsHref(repo: string): string {
  return `/workspace/contracts?${new URLSearchParams({ repo }).toString()}`;
}

export default function SystemMapPage() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // One wave above the fold: the graph, repo health, and the three small
  // reports the lede, the lens counts and "Needs attention" all read. Blast
  // radius and per-repo contracts are fetched only when asked for.
  const { data: graph, isLoading, error } = useWorkspaceSystemGraph();
  const { data: repoGraph } = useWorkspaceGraph();
  const { data: architecture, error: architectureError } = useWorkspaceArchitecture();
  const { data: conformance, error: conformanceError } = useWorkspaceConformance();
  const { data: breaking, error: breakingError } = useWorkspaceBreakingChanges();

  // Selection, lens and blast target live in the URL, so a view can be linked to.
  const selection = useMemo<SystemMapSelection>(() => {
    const node = searchParams.get("node");
    if (node) return { type: "node", id: node };
    const edge = searchParams.get("edge");
    if (edge) return { type: "edge", id: edge };
    return null;
  }, [searchParams]);
  const lensOptions = useMemo(
    () =>
      lensOptionsFor(graph, breaking, conformance, architecture, {
        breaking: Boolean(breakingError),
        conformance: Boolean(conformanceError),
        architecture: Boolean(architectureError),
      }),
    [graph, breaking, conformance, architecture, breakingError, conformanceError, architectureError],
  );
  // A lens from the URL that cannot act here (0 findings, or its report
  // failed) reads as no lens, rather than a checked option nobody can reach.
  const lensParam = searchParams.get("lens") as SystemMapLens | null;
  const lens: SystemMapLens =
    lensParam && LENSES.includes(lensParam) && !lensOptions.find((o) => o.value === lensParam)?.disabledReason
      ? lensParam
      : "none";
  const blastTarget = lens === "blast" ? searchParams.get("from") : null;

  const replaceParams = useCallback(
    (edit: (p: URLSearchParams) => void) => {
      const params = new URLSearchParams(searchParams.toString());
      edit(params);
      const query = params.toString();
      // `replace`, not `push`: exploring a diagram should not fill the back button.
      router.replace(query ? `?${query}` : "/workspace/system-map", { scroll: false });
    },
    [router, searchParams],
  );

  const onSelectionChange = useCallback(
    (next: SystemMapSelection) =>
      replaceParams((p) => {
        p.delete("node");
        p.delete("edge");
        if (next) p.set(next.type, next.id);
      }),
    [replaceParams],
  );

  const setLens = useCallback(
    (next: SystemMapLens, from?: string | null) =>
      replaceParams((p) => {
        if (next === "none") p.delete("lens");
        else p.set("lens", next);
        if (next === "blast" && from !== undefined) {
          if (from) p.set("from", from);
          else p.delete("from");
        } else if (next !== "blast") p.delete("from");
      }),
    [replaceParams],
  );

  const [includeBehavioral, setIncludeBehavioral] = useState(true);
  const { data: blast, isLoading: blastLoading, error: blastError } = useWorkspaceBlastRadius(blastTarget, { includeBehavioral });

  const focusRepo = useMemo(() => selectionRepo(graph, selection), [graph, selection]);
  const { data: repoContracts, isLoading: repoContractsLoading } = useRepoContracts(focusRepo);

  const roleByNodeId = useMemo<Map<string, NodeArchitectureRole>>(() => {
    const m = new Map<string, NodeArchitectureRole>();
    for (const r of architecture?.roles ?? []) m.set(r.id, r);
    return m;
  }, [architecture]);

  // Repo health by alias; the repo graph's node `name` is the alias.
  const { healthByRepo, repoIdByAlias } = useMemo(() => {
    const health = new Map<string, RepoHealth>();
    const ids = new Map<string, string>();
    for (const n of repoGraph?.nodes ?? []) {
      health.set(n.name, { score: n.health_score, source: n.health_score_source });
      ids.set(n.name, n.repo_id);
    }
    return { healthByRepo: health, repoIdByAlias: ids };
  }, [repoGraph]);

  const overlay = useMemo(() => {
    if (!graph) return undefined;
    if (lens === "blast" && blast) return buildBlastRadiusOverlay(graph, blast);
    if (lens === "breaking" && breaking) return buildBreakingChangeOverlay(graph, breaking);
    if (lens === "conformance" && conformance) return buildConformanceOverlay(graph, conformance);
    if (lens === "core" && architecture) {
      // Nodes already name their role; a "core" badge would say it twice.
      const { nodeBadges: _named, ...core } = buildArchitectureOverlay(graph, architecture);
      return core;
    }
    return undefined;
  }, [graph, lens, blast, breaking, conformance, architecture]);

  const drawer = useMemo(
    () => ({
      repoContracts,
      repoContractsLoading,
      cycles: conformance?.cycles ?? [],
      contractHref,
      contractsHref,
      repoHref: (alias: string) => {
        const id = repoIdByAlias.get(alias);
        return id ? `/repos/${id}/overview` : null;
      },
      coChangesHref: "/workspace/co-changes",
      LinkComponent: Link,
      onShowBlastRadius: (id: string) => setLens("blast", id),
    }),
    [repoContracts, repoContractsLoading, conformance, repoIdByAlias, setLens],
  );

  return (
    <PageShell
      title="System map"
      icon={<Waypoints className="h-5 w-5 text-[var(--color-text-tertiary)]" />}
      description="Services and the typed relationships between them, rebuilt from code and git history on every workspace update."
      maxWidth="wide"
    >
      {graph && <Lede graph={graph} architecture={architecture} conformance={conformance} />}
      {graph && <StatRibbon stats={ribbon(graph, architecture)} LinkComponent={Link} />}

      <OverviewSection
        title="Services and relationships"
        description="Each box is a service; each line is a contract, import or co-change between two of them. Pick a lens to ask the map one question at a time."
      >
        {isLoading ? (
          // Same rows as the loaded map: lens, filters, legend, then the canvas.
          <div className="flex flex-col gap-3" aria-hidden>
            <Skeleton className="h-8 w-[480px] max-w-full" />
            <Skeleton className="h-8 w-[640px] max-w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className={`w-full rounded-lg ${SYSTEM_MAP_CANVAS_HEIGHT}`} />
          </div>
        ) : (
          <SystemMap
            graph={graph}
            error={error ?? null}
            healthByRepo={healthByRepo}
            roleByNodeId={roleByNodeId}
            {...(overlay ? { overlay } : {})}
            selection={selection}
            onSelectionChange={onSelectionChange}
            drawer={drawer}
            toolbar={
              <SystemMapLensControl
                value={lens}
                options={lensOptions}
                onChange={(next) => setLens(next, next === "blast" ? (selection?.type === "node" ? selection.id : null) : undefined)}
                nodes={graph?.nodes ?? []}
                blastTarget={blastTarget}
                onBlastTargetChange={(id) => setLens("blast", id)}
                includeBehavioral={includeBehavioral}
                onIncludeBehavioralChange={setIncludeBehavioral}
              />
            }
          />
        )}
      </OverviewSection>

      {graph && (
        <SystemMapLensResults
          lens={lens}
          graph={graph}
          selection={selection}
          onSelect={onSelectionChange}
          blastTarget={blastTarget}
          blast={blast}
          blastLoading={blastLoading}
          blastError={Boolean(blastError)}
          includeBehavioral={includeBehavioral}
          breaking={breaking}
          conformance={conformance}
          architecture={architecture}
          conformanceHref="/workspace/conformance"
          LinkComponent={Link}
        />
      )}

      {graph && (
        <SystemMapFindings
          graph={graph}
          conformance={conformance}
          breaking={breaking}
          diagnostics={graph.diagnostics}
          conformanceError={Boolean(conformanceError)}
          breakingError={Boolean(breakingError)}
          onSelect={onSelectionChange}
          onLensChange={(next) => setLens(next)}
          unmatchedHref="/workspace/contracts?role=consumer&linked=no"
          conformanceHref="/workspace/conformance"
          LinkComponent={Link}
        />
      )}
    </PageShell>
  );
}

/** Counts and disabled reasons for each lens, from data already on the page. */
function lensOptionsFor(
  graph: SystemGraph | null,
  breaking: ReturnType<typeof useWorkspaceBreakingChanges>["data"],
  conformance: ConformanceReport | null,
  architecture: ReturnType<typeof useWorkspaceArchitecture>["data"],
  failed: { breaking: boolean; conformance: boolean; architecture: boolean },
): SegmentOption<SystemMapLens>[] {
  const hasEdges = (graph?.edges.length ?? 0) > 0;
  const breakingTotal = breaking ? breaking.breaking_count + breaking.warning_count : 0;
  const findings = conformance ? conformance.violation_count + (conformance.total_cycles ?? conformance.cycle_count) : 0;
  return [
    { value: "none", label: "None", hint: "The map as extracted, no lens" },
    {
      value: "blast",
      label: "Blast radius",
      hint: "What depends on one service, directly or through others",
      disabledReason: hasEdges ? undefined : "No relationships to trace yet.",
    },
    {
      value: "breaking",
      label: "Breaking changes",
      count: breaking ? String(breakingTotal) : undefined,
      disabledReason: failed.breaking
        ? "Could not load the breaking-change report."
        : !breaking
        ? "Loading the breaking-change report."
        : !breaking.generated_at
          ? "Breaking-change detection has not run. Run a workspace update."
          : breakingTotal === 0
            ? "No provider contract changed incompatibly in the most recent update."
            : undefined,
    },
    {
      value: "conformance",
      label: "Conformance",
      count: conformance ? String(findings) : undefined,
      hint: "Rule violations and dependency cycles",
      disabledReason: failed.conformance
        ? "Could not load the conformance report."
        : !conformance
        ? "Loading the conformance report."
        : !conformance.generated_at
          ? "Conformance has not been checked. Run a workspace update."
          : findings === 0
            ? "No rule violations or dependency cycles."
            : undefined,
    },
    {
      value: "core",
      label: "Core",
      count: architecture ? String(architecture.core_size) : undefined,
      hint: "The largest group of services that all depend on each other, and every service's role",
      disabledReason: failed.architecture
        ? "Could not load the architecture metrics."
        : !architecture
          ? "Loading architecture metrics."
          : architecture.core_size === 0 && architecture.roles.length === 0
            ? "No services to classify."
            : undefined,
    },
  ];
}

function Lede({
  graph,
  architecture,
  conformance,
}: {
  graph: SystemGraph;
  architecture: ReturnType<typeof useWorkspaceArchitecture>["data"];
  conformance: ConformanceReport | null;
}) {
  const name = (id: string) => graph.nodes.find((n) => n.id === id)?.name ?? id;
  const repos = new Set(graph.nodes.map((n) => n.repo)).size;
  const structural = graph.edges.filter((e) => e.structural).length;
  const behavioral = graph.edges.length - structural;
  const cycles = conformance?.cycles ?? [];
  const totalCycles = conformance ? (conformance.total_cycles ?? conformance.cycle_count) : 0;
  const first = cycles[0];

  return (
    <PageLede
      label="Architecture score"
      labelHint="A 1 to 10 roll-up of propagation cost, the size of the cyclic core, dependency cycles and rule violations, over structural edges only. Higher means less coupled."
      value={architecture ? architecture.score.toFixed(1) : "–"}
      unit="out of 10"
      layout="beside"
    >
      <p>
        {plural(graph.nodes.length, "service", "services")} in {plural(repos, "repository", "repositories")}, joined by{" "}
        {plural(graph.edges.length, "relationship", "relationships")}: {structural} structural (a contract or import
        in code) and {behavioral} co-change (files that change in the same commits, not a call).
      </p>
      {first ? (
        <p>
          <span className="font-medium text-[var(--color-text-primary)]">
            {first.nodes.map(name).join(" and ")} depend on each other
          </span>
          , a dependency cycle: none of them can change a contract, build or deploy alone.
          {totalCycles > 1 ? ` ${plural(totalCycles - 1, "more cycle is", "more cycles are")} listed under Needs attention.` : " It is listed under Needs attention with a prompt to break it."}
        </p>
      ) : conformance?.generated_at ? (
        <p>No dependency cycles: every service can change without a loop back to itself.</p>
      ) : null}
      {architecture && (
        <p>
          On average a service reaches {architecture.propagation_cost_pct.toFixed(1)}% of the others through
          dependencies, and {architecture.core_size > 0 ? `${plural(architecture.core_size, "service forms", "services form")} the cyclic core` : "there is no cyclic core"}
          . Those two figures drive the score.
        </p>
      )}
    </PageLede>
  );
}

function ribbon(
  graph: SystemGraph,
  architecture: ReturnType<typeof useWorkspaceArchitecture>["data"],
): RibbonStat[] {
  const diag = graph.diagnostics;
  const repos = new Set(graph.nodes.map((n) => n.repo)).size;
  const structural = graph.edges.filter((e) => e.structural).length;
  return [
    {
      label: "Services",
      value: formatNumber(graph.nodes.length),
      sub: `in ${plural(repos, "repository", "repositories")}`,
    },
    {
      label: "Relationships",
      value: formatNumber(graph.edges.length),
      sub: `${structural} structural, ${graph.edges.length - structural} co-change`,
    },
    {
      label: "Propagation cost",
      value: architecture ? `${architecture.propagation_cost_pct.toFixed(1)}%` : "–",
      sub: "of other services the average one reaches",
      hint: "The share of other services the average service can reach through structural dependencies. 0% is fully decoupled, 100% is everything reaching everything.",
    },
    {
      label: "Contract links",
      value: diag ? formatNumber(diag.total_links) : "–",
      sub: diag ? `from ${formatNumber(diag.total_providers)} providers, ${formatNumber(diag.total_consumers)} consumers` : undefined,
      hint: "Consumer calls matched to the provider that serves them, across services.",
    },
    {
      label: "Unmatched consumers",
      value: diag ? formatNumber(diag.unmatched_consumers.length) : "–",
      sub: "calls with no provider in the workspace",
      hint: "Consumer contracts that matched no provider: missing, a third-party host, or inside the same service.",
    },
  ];
}
