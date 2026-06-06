import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type KeyboardEvent,
  type SetStateAction,
} from "react";
import Fuse from "fuse.js";
import type Graph from "graphology";
import type { SigmaEdgeAttributes, SigmaNodeAttributes } from "./sigma/types";

type SigmaGraph = Graph<SigmaNodeAttributes, SigmaEdgeAttributes>;

interface GraphSearchOptions {
  sigmaGraph: SigmaGraph | null;
  hideTests: boolean;
  panToNode: (nodeId: string) => void;
  setSelectedNodeId: Dispatch<SetStateAction<string | null>>;
}

/**
 * Fuzzy node search over the rendered Sigma graph: a debounced Fuse query that
 * dims non-matching nodes, plus arrow/enter/escape keyboard navigation of the
 * result set. Returns the search query state, the dimmed-node set, the result
 * list, and the input keydown handler.
 */
export function useGraphSearch(opts: GraphSearchOptions) {
  const { sigmaGraph, hideTests, panToNode, setSelectedNodeId } = opts;

  const [searchQuery, setSearchQuery] = useState("");
  const [searchDimmedNodes, setSearchDimmedNodes] = useState<Set<string> | null>(null);
  const [searchResults, setSearchResults] = useState<string[]>([]);
  const [searchResultIndex, setSearchResultIndex] = useState(0);

  // The index is built over the *rendered* sigma graph. In the constellation
  // scope this means hub labels plus, once a hub is expanded, its satellite
  // (member file) nodes — the memo re-runs whenever the merged graph changes, so
  // expanding a hub makes its members searchable automatically. LIMITATION:
  // member file names are NOT indexed before their hub is expanded (full member
  // lists aren't fetched until expansion), so a pre-expansion search matches hub
  // labels only.
  const fuseIndex = useMemo(() => {
    if (!sigmaGraph)
      return new Fuse<{ id: string; label: string }>([], {
        keys: ["id", "label"],
        threshold: 0.4,
      });
    const items: { id: string; label: string }[] = [];
    sigmaGraph.forEachNode((id, attrs) => {
      if (hideTests && attrs.isTest) return;
      items.push({ id, label: attrs.label });
    });
    return new Fuse(items, { keys: ["id", "label"], threshold: 0.4 });
  }, [sigmaGraph, hideTests]);

  const searchTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => {
    if (!searchQuery || searchQuery.length < 2) {
      clearTimeout(searchTimerRef.current);
      setSearchDimmedNodes(null);
      setSearchResults([]);
      setSearchResultIndex(0);
      return;
    }
    clearTimeout(searchTimerRef.current);
    searchTimerRef.current = setTimeout(() => {
      const results = fuseIndex.search(searchQuery);
      const matchIds = new Set(results.map((r) => r.item.id));
      const ids = results.map((r) => r.item.id);
      const dimmed = new Set<string>();

      if (sigmaGraph) {
        sigmaGraph.forEachNode((nodeId) => {
          if (!matchIds.has(nodeId)) dimmed.add(nodeId);
        });
      }

      setSearchDimmedNodes(dimmed);
      setSearchResults(ids);
      setSearchResultIndex(0);

      if (ids.length === 1) {
        setSelectedNodeId(ids[0]!);
      }

      if (ids.length > 0 && ids.length <= 20) {
        panToNode(ids[0]!);
      }
    }, 150);
    return () => clearTimeout(searchTimerRef.current);
  }, [searchQuery, fuseIndex, sigmaGraph, panToNode, setSelectedNodeId]);

  const handleSearchKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (searchResults.length === 0) {
        if (e.key === "Escape") {
          setSearchQuery("");
        }
        return;
      }
      switch (e.key) {
        case "ArrowDown": {
          e.preventDefault();
          const next = (searchResultIndex + 1) % searchResults.length;
          setSearchResultIndex(next);
          panToNode(searchResults[next]!);
          break;
        }
        case "ArrowUp": {
          e.preventDefault();
          const prev =
            (searchResultIndex - 1 + searchResults.length) % searchResults.length;
          setSearchResultIndex(prev);
          panToNode(searchResults[prev]!);
          break;
        }
        case "Enter": {
          e.preventDefault();
          const id = searchResults[searchResultIndex];
          if (id) {
            setSelectedNodeId(id);
            panToNode(id);
          }
          break;
        }
        case "Escape":
          setSearchQuery("");
          break;
      }
    },
    [searchResults, searchResultIndex, panToNode, setSelectedNodeId],
  );

  return { searchQuery, setSearchQuery, searchResults, searchDimmedNodes, handleSearchKeyDown };
}
