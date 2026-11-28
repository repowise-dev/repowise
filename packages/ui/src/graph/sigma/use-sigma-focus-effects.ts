import { useEffect, type MutableRefObject } from "react";
import type Sigma from "sigma";
import type Graph from "graphology";
import type { SigmaNodeAttributes, SigmaEdgeAttributes } from "./types";

/**
 * First slice of the `useSigmaRenderer` split (#6565): the focus and
 * highlight effects.
 *
 * `useSigmaRenderer` (use-sigma.ts) had a cyclomatic complexity of 74 across
 * 369 lines, with 8 `useEffect`s reading and writing the same refs — so it
 * was hard to tell which effect owns which state. This hook owns the two
 * effects that keep the interaction state the focus computation and the
 * node/edge reducers read:
 *
 * 1. Sync the interaction-state refs (selection, highlighted path/edges, dim
 *    sets, hidden nodes), recompute the focus, and repaint.
 * 2. Pre-hide edges by type on the graphology graph, then recompute the
 *    focus — a focused file's revealed edges follow the same toggles.
 *
 * Pure move, no behaviour change: the effect bodies and dependency arrays
 * are verbatim, and `useSigmaRenderer` calls this hook at the exact position
 * the two effects used to occupy, so effect order is unchanged.
 *
 * The refs are passed in (not created here) because the Sigma reducers in
 * `useSigmaRenderer` read the same ref objects outside React; creating a
 * second set here would silently desync them.
 */
export interface UseSigmaFocusEffectsOptions {
  graph: Graph<SigmaNodeAttributes, SigmaEdgeAttributes> | null;
  selectedNodeId: string | null;
  highlightedPath: Set<string>;
  highlightedEdges: Set<string>;
  searchDimmedNodes: Set<string> | null;
  communityDimmedNodes: Set<string> | null;
  expandDimmedNodes?: Set<string> | null | undefined;
  hiddenNodes?: Set<string> | undefined;
  visibleEdgeTypes?: Set<string> | undefined;
}

export interface UseSigmaFocusEffectsRefs {
  sigmaRef: MutableRefObject<Sigma | null>;
  selectedRef: MutableRefObject<string | null>;
  highlightedPathRef: MutableRefObject<Set<string>>;
  highlightedEdgesRef: MutableRefObject<Set<string>>;
  searchDimmedRef: MutableRefObject<Set<string> | null>;
  communityDimmedRef: MutableRefObject<Set<string> | null>;
  expandDimmedRef: MutableRefObject<Set<string> | null>;
  hiddenNodesRef: MutableRefObject<Set<string> | undefined>;
  /** Recompute which file is focused and repaint what changed. */
  applyFocusRef: MutableRefObject<(partial: boolean) => void>;
}

export function useSigmaFocusEffects(
  options: UseSigmaFocusEffectsOptions,
  refs: UseSigmaFocusEffectsRefs,
): void {
  const {
    sigmaRef,
    selectedRef,
    highlightedPathRef,
    highlightedEdgesRef,
    searchDimmedRef,
    communityDimmedRef,
    expandDimmedRef,
    hiddenNodesRef,
    applyFocusRef,
  } = refs;

  // Sync interaction state refs (no color work here — that's in the graph effect)
  useEffect(() => {
    selectedRef.current = options.selectedNodeId;
    highlightedPathRef.current = options.highlightedPath;
    highlightedEdgesRef.current = options.highlightedEdges;
    searchDimmedRef.current = options.searchDimmedNodes;
    communityDimmedRef.current = options.communityDimmedNodes;
    expandDimmedRef.current = options.expandDimmedNodes ?? null;
    hiddenNodesRef.current = options.hiddenNodes;
    applyFocusRef.current(false);
    sigmaRef.current?.refresh();
  }, [
    options.selectedNodeId,
    options.highlightedPath,
    options.highlightedEdges,
    options.searchDimmedNodes,
    options.communityDimmedNodes,
    options.expandDimmedNodes,
    options.hiddenNodes,
  ]);

  // Pre-hide edges by type on the graphology graph (batch: 1 event instead of N)
  useEffect(() => {
    const graph = options.graph;
    if (!graph || graph.size === 0) return;
    const visibleTypes = options.visibleEdgeTypes;
    graph.updateEachEdgeAttributes(
      (_edge, attrs) => {
        const shouldHide = visibleTypes ? !visibleTypes.has(attrs.edgeKind) : false;
        if (attrs.hidden === shouldHide) return attrs;
        return { ...attrs, hidden: shouldHide };
      },
      { attributes: ["hidden"] },
    );
    // The focused file's revealed edges follow the same toggles.
    applyFocusRef.current(false);
  }, [options.visibleEdgeTypes, options.graph]);
}
