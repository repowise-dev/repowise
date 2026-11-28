/**
 * The Architecture map's view state: the axes the web page keeps in its URL
 * (scope, signal, module, community, population, external nodes, node cap,
 * colour mode) as plain state, with the handlers that reset them the way the
 * web page's handlers reset their params.
 */

import { useCallback, useState } from "react";
import type { GraphFlowProps } from "@repowise-dev/ui/graph/graph-flow";
import type { GraphScope } from "@repowise-dev/ui/graph/graph-scope-controls";
import type { ModuleGroup } from "@repowise-dev/ui/graph/use-module-filter";
import { PRODUCTION_ONLY, type GraphPopulation } from "@repowise-dev/types/graph";
import type { ViewMode } from "./use-architecture-data";

export type Scope = "communities" | "files";
type Signal = "dead" | "hot" | null;
type GraphColorMode = NonNullable<GraphFlowProps["colorMode"]>;

/** Scope + signal -> the canvas's internal ViewMode, as the web maps them. */
function toViewMode(scope: Scope, signal: Signal): ViewMode {
  if (scope === "communities") return "architecture";
  if (signal === "dead") return "dead";
  if (signal === "hot") return "hotfiles";
  return "full";
}

function toSignal(mode: ViewMode): Signal {
  if (mode === "dead") return "dead";
  if (mode === "hotfiles") return "hot";
  return null;
}

export function useMapState(selectPath: string | undefined) {
  // The launch file pins the file scope, the way a `?node=` link does on the
  // web, and is dropped on the first scope or community change so a later
  // remount of the canvas does not reselect it.
  const [initialNode, setInitialNode] = useState<string | null>(selectPath ?? null);
  const [colorMode, setColorMode] = useState<GraphColorMode>("community");
  // The web page lands on Files.
  const [graphScope, setGraphScope] = useState<GraphScope>("files");
  const [signal, setSignal] = useState<Signal>(null);
  const [activeModule, setActiveModule] = useState<string | null>(null);
  const [activeCommunity, setActiveCommunity] = useState<number | null>(null);
  const [population, setPopulation] = useState<GraphPopulation>(PRODUCTION_ONLY);
  const [showExternal, setShowExternal] = useState(false);
  const [graphLimit, setGraphLimit] = useState<number | undefined>(undefined);
  const [moduleGroups, setModuleGroups] = useState<ModuleGroup[]>([]);
  const [flowsRequested, setFlowsRequested] = useState(false);

  const tree = graphScope === "tree";
  const scope: Scope = graphScope === "communities" ? "communities" : "files";

  const handleScopeChange = useCallback((next: GraphScope) => {
    // Narrowing is a file-scope concept and does not survive a scope change.
    setActiveModule(null);
    setActiveCommunity(null);
    setInitialNode(null);
    setGraphScope(next);
  }, []);

  // Entering a community implies the file scope; leaving returns to the
  // constellation it came from.
  const handleActiveCommunityChange = useCallback((next: number | null) => {
    setActiveCommunity(next);
    setInitialNode(null);
    if (next === null) {
      setGraphScope("communities");
      return;
    }
    setActiveModule(null);
    // A slice was never filtered to dead or hot files.
    setSignal(null);
    setGraphScope("files");
  }, []);

  const handleNarrowingChange = useCallback(
    ({ module, community }: { module: string | null; community: number | null }) => {
      if (community !== null) {
        handleActiveCommunityChange(community);
        return;
      }
      setActiveCommunity(null);
      setActiveModule(module);
    },
    [handleActiveCommunityChange],
  );

  const handleViewModeChange = useCallback((mode: ViewMode) => setSignal(toSignal(mode)), []);

  const handleFlowsVisibilityChange = useCallback((visible: boolean) => {
    if (visible) setFlowsRequested(true);
  }, []);

  return {
    initialNode,
    colorMode,
    setColorMode,
    tree,
    scope,
    viewMode: toViewMode(scope, signal),
    signal,
    activeModule,
    activeCommunity,
    population,
    setPopulation,
    showExternal,
    setShowExternal,
    graphLimit,
    setGraphLimit,
    moduleGroups,
    setModuleGroups,
    flowsRequested,
    handleScopeChange,
    handleActiveCommunityChange,
    handleNarrowingChange,
    handleViewModeChange,
    handleFlowsVisibilityChange,
  };
}

export type MapState = ReturnType<typeof useMapState>;
