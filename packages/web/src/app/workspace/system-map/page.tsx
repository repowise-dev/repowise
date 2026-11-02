"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { Waypoints } from "lucide-react";
import {
  SystemMap,
  SYSTEM_MAP_CANVAS_HEIGHT,
  SystemMapEvidenceNotice,
  SystemMapFindings,
  SystemMapLensControl,
  SystemMapLensResults,
  buildArchitectureOverlay,
  buildBlastRadiusOverlay,
  buildBreakingChangeOverlay,
  buildConformanceOverlay,
  drawnFamilies,
  parseMapView,
  selectionRepo,
  writeMapView,
  type MapViewState,
  type ContractRef,
  type RepoHealth,
  type SegmentOption,
  type SystemMapLens,
  type SystemMapSelection,
} from "@repowise-dev/ui/workspace/system-map";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { StatRibbon } from "@repowise-dev/ui/stats/stat-ribbon";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import type { ConformanceReport, NodeArchitectureRole, SystemGraph } from "@/lib/api/types";
import {
  useWorkspaceSystemGraph,
  useWorkspaceDiagnostics,
  useWorkspaceGraph,
  useWorkspaceBlastRadius,
  useWorkspaceBreakingChanges,
  useWorkspaceConformance,
  useWorkspaceArchitecture,
} from "@/lib/hooks/use-workspace";
import { markSystemMap, useSystemMapPerfMarks } from "@/lib/system-map-perf";
import { mapLede, mapRibbon, useListJoin, type Translator } from "./map-summary";
import { useRepoContracts } from "./use-repo-contracts";
import { useServiceLinks } from "./use-service-links";
import { loadRouteLinks, useSystemMapNavCopy } from "./use-system-map-nav";

const LENSES: readonly SystemMapLens[] = ["none", "blast", "breaking", "conformance", "core"];
/** URL params a selection is written to, one per selection type. */
const SELECTION_PARAMS = ["node", "edge", "group", "band"] as const;

function contractHref(ref: ContractRef): string {
  const params = new URLSearchParams({ contract: ref.contract_id, repo: ref.repo });
  if (ref.file_path) params.set("file", ref.file_path);
  return `/workspace/contracts?${params.toString()}`;
}

function contractsHref(repo: string): string {
  return `/workspace/contracts?${new URLSearchParams({ repo }).toString()}`;
}

export default function SystemMapPage() {
  const t = useTranslations("systemMap");
  const tLinks = useTranslations("linkCoverage");
  const navCopy = useSystemMapNavCopy();
  const join = useListJoin();
  const router = useRouter();
  const searchParams = useSearchParams();

  // One wave above the fold: the map's lean graph, repo health, and the three
  // small reports the lede, the lens counts and "Needs attention" all read.
  // Diagnostics follow the graph; a server without the lean view sends them
  // inside it. Blast radius and per-repo contracts are fetched only when asked for.
  const { data: graph, isLoading, error } = useWorkspaceSystemGraph("map");
  const { data: fetchedDiagnostics } = useWorkspaceDiagnostics(Boolean(graph && !graph.diagnostics));
  const diagnostics = graph?.diagnostics ?? fetchedDiagnostics;
  const { data: repoGraph } = useWorkspaceGraph();
  const { data: architecture, error: architectureError } = useWorkspaceArchitecture();
  const { data: conformance, error: conformanceError } = useWorkspaceConformance();
  const { data: breaking, error: breakingError } = useWorkspaceBreakingChanges();

  useSystemMapPerfMarks(graph);

  // Selection, level, lens and blast target live in the URL, so a view can be linked to.
  const selection = useMemo<SystemMapSelection>(() => {
    for (const type of SELECTION_PARAMS) {
      const id = searchParams.get(type);
      if (id) return { type, id };
    }
    return null;
  }, [searchParams]);
  const mapView = useMemo(() => parseMapView((key) => searchParams.get(key)), [searchParams]);
  const lensOptions = useMemo(
    () =>
      lensOptionsFor(t, graph, breaking, conformance, architecture, {
        breaking: Boolean(breakingError),
        conformance: Boolean(conformanceError),
        architecture: Boolean(architectureError),
      }),
    [
      t,
      graph,
      breaking,
      conformance,
      architecture,
      breakingError,
      conformanceError,
      architectureError,
    ],
  );
  // A lens from the URL that cannot act here (0 findings, or its report
  // failed) reads as no lens, rather than a checked option nobody can reach.
  const lensParam = searchParams.get("lens") as SystemMapLens | null;
  const lens: SystemMapLens =
    lensParam && LENSES.includes(lensParam) && !lensOptions.find((o) => o.value === lensParam)?.disabledReason
      ? lensParam
      : "none";
  const blastTarget = lens === "blast" ? searchParams.get("from") : null;

  // The query last written but not yet rendered: a second edit in the same
  // tick builds on it, so neither edit is lost.
  const pendingQuery = useRef<string | null>(null);
  useEffect(() => {
    pendingQuery.current = null;
  }, [searchParams]);
  const replaceParams = useCallback(
    (edit: (p: URLSearchParams) => void) => {
      const params = new URLSearchParams(pendingQuery.current ?? searchParams.toString());
      edit(params);
      const query = params.toString();
      pendingQuery.current = query;
      // `replace`, not `push`: exploring a diagram should not fill the back button.
      router.replace(query ? `?${query}` : "/workspace/system-map", { scroll: false });
    },
    [router, searchParams],
  );

  const onSelectionChange = useCallback(
    (next: SystemMapSelection) =>
      replaceParams((p) => {
        for (const key of SELECTION_PARAMS) p.delete(key);
        if (next) p.set(next.type, next.id);
      }),
    [replaceParams],
  );
  const onViewChange = useCallback(
    (next: MapViewState) => replaceParams((p) => writeMapView(p, next)),
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
  const loadLinksBetween = useServiceLinks();

  const roleByNodeId = useMemo<Map<string, NodeArchitectureRole>>(() => {
    const m = new Map<string, NodeArchitectureRole>();
    for (const r of architecture?.roles ?? []) m.set(r.id, r);
    return m;
  }, [architecture]);

  // Repo health by alias; the repo graph's node `name` is the alias.
  const { healthByRepo, repoIdByAlias } = useMemo(() => {
    const nodes = repoGraph?.nodes ?? [];
    // An unscored repo has no entry, so its nodes read "No health score".
    const health = new Map<string, RepoHealth>(
      nodes.flatMap((n) =>
        n.health_score == null ? [] : [[n.name, { score: n.health_score }] as const],
      ),
    );
    const ids = new Map<string, string>(nodes.map((n) => [n.name, n.repo_id]));
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
      diagnostics,
    }),
    [repoContracts, repoContractsLoading, conformance, repoIdByAlias, setLens, diagnostics],
  );

  return (
    <PageShell
      title={t("title")}
      icon={<Waypoints />}
      // One sentence above the map, stated for the families it draws; the figures follow the map.
      description={mapLede(t, join, graph, drawnFamilies(mapView)) ?? t("description")}
      maxWidth="wide"
    >
      {/* The map is the page's subject: its heading shares the lens row, straight under the lede. */}
      <section className="flex flex-col gap-3" aria-label={t("section.title")}>
        {/* Said once, beside the map it qualifies; the score below reads "–" when not determined. */}
        <SystemMapEvidenceNotice
          evidence={architecture?.evidence}
          partialLabel={t("evidence.partial")}
          insufficientLabel={t("evidence.insufficient")}
        />
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
            view={mapView}
            onViewChange={onViewChange}
            title={t("section.title")}
            estateLabel={t("estate")}
            onPhase={markSystemMap}
            drawer={drawer}
            loadLinksBetween={loadLinksBetween}
            loadRoutes={loadRouteLinks}
            navCopy={navCopy}
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
      </section>

      {graph && <StatRibbon stats={mapRibbon(t, tLinks, join, graph, architecture, diagnostics)} LinkComponent={Link} />}

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
          diagnostics={diagnostics}
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
  t: Translator,
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
    { value: "none", label: t("lens.none.label"), hint: t("lens.none.hint") },
    {
      value: "blast",
      label: t("lens.blast.label"),
      hint: t("lens.blast.hint"),
      disabledReason: hasEdges ? undefined : t("lens.blast.disabled"),
    },
    {
      value: "breaking",
      label: t("lens.breaking.label"),
      count: breaking ? String(breakingTotal) : undefined,
      disabledReason: failed.breaking
        ? t("lens.breaking.disabledFailed")
        : !breaking
        ? t("lens.breaking.disabledLoading")
        : !breaking.generated_at
          ? t("lens.breaking.disabledNotRun")
          : breakingTotal === 0
            ? t("lens.breaking.disabledNone")
            : undefined,
    },
    {
      value: "conformance",
      label: t("lens.conformance.label"),
      count: conformance ? String(findings) : undefined,
      hint: t("lens.conformance.hint"),
      disabledReason: failed.conformance
        ? t("lens.conformance.disabledFailed")
        : !conformance
        ? t("lens.conformance.disabledLoading")
        : !conformance.generated_at
          ? t("lens.conformance.disabledNotRun")
          : findings === 0
            ? t("lens.conformance.disabledNone")
            : undefined,
    },
    {
      value: "core",
      label: t("lens.core.label"),
      // Null when the graph's evidence is insufficient; the lens then says why.
      count: architecture?.core_size != null ? String(architecture.core_size) : undefined,
      hint: t("lens.core.hint"),
      disabledReason: failed.architecture
        ? t("lens.core.disabledFailed")
        : !architecture
          ? t("lens.core.disabledLoading")
          : architecture.core_size === 0 && architecture.roles.length === 0
            ? t("lens.core.disabledNone")
            : undefined,
    },
  ];
}
