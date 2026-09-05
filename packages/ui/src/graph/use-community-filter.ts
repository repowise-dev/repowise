import { useCallback, useMemo, useState } from "react";
import type Graph from "graphology";
import type { SigmaEdgeAttributes, SigmaNodeAttributes } from "./sigma/types";

type SigmaGraph = Graph<SigmaNodeAttributes, SigmaEdgeAttributes>;

/**
 * Community-based filtering: which communities are active and which file nodes
 * are dimmed because their community is filtered out. ``activeCommunities ===
 * null`` means "all communities" (no filter). Derived entirely from the graph
 * plus this hook's own state — the community *detail panel* is a separate
 * concern and stays in the parent.
 */
export function useCommunityFilter(sigmaGraph: SigmaGraph | null) {
  const [activeCommunities, setActiveCommunities] = useState<Set<number> | null>(null);

  const communityDimmedNodes = useMemo(() => {
    if (!activeCommunities) return null;
    const dimmed = new Set<string>();
    if (sigmaGraph) {
      sigmaGraph.forEachNode((nodeId, attrs) => {
        if (attrs.nodeType === "file" && !activeCommunities.has(attrs.communityId)) {
          dimmed.add(nodeId);
        }
      });
    }
    return dimmed.size > 0 ? dimmed : null;
  }, [activeCommunities, sigmaGraph]);

  const allCommunityIds = useMemo(() => {
    const ids = new Set<number>();
    if (sigmaGraph) {
      sigmaGraph.forEachNode((_nodeId, attrs) => {
        if (attrs.nodeType === "file") ids.add(attrs.communityId);
      });
    }
    return ids;
  }, [sigmaGraph]);

  const handleCommunityToggle = useCallback(
    (cid: number) => {
      // A community with nothing on this canvas has nothing to toggle. The
      // key used to list communities from the repo-wide summary, so clicking
      // one that was not drawn added a phantom id: nothing dimmed, the swatch
      // stayed filled, and the phantom then made `next.size` match and reset
      // the filter to "show all" — the opposite of the label.
      if (!allCommunityIds.has(cid)) return;
      setActiveCommunities((prev) => {
        // Re-intersected every time: the drawn set changes on a signal toggle,
        // a module filter, an ego depth and a load-more, and an id left over
        // from a graph that is gone made the size check below fire on the
        // wrong click.
        const next = new Set(
          prev ? [...prev].filter((id) => allCommunityIds.has(id)) : allCommunityIds,
        );
        if (next.has(cid)) next.delete(cid);
        else next.add(cid);
        if (next.size === allCommunityIds.size) return null;
        return next;
      });
    },
    [allCommunityIds],
  );

  const handleToggleAllCommunities = useCallback((selectAll: boolean) => {
    setActiveCommunities(selectAll ? null : new Set<number>());
  }, []);

  return {
    activeCommunities,
    communityDimmedNodes,
    /** Communities with at least one node on the canvas. The key filters its
     *  rows by this so it cannot offer a toggle that does nothing. */
    drawnCommunityIds: allCommunityIds,
    handleCommunityToggle,
    handleToggleAllCommunities,
  };
}
