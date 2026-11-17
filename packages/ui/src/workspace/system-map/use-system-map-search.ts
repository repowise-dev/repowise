"use client";

/**
 * Search state for the map's combobox, on the `useGraphSearch` pattern: a
 * debounced query, Fuse over services and groups, routes through the host's
 * loader, and arrow / Enter / Escape over one flat result list. Picking a
 * result hands the level it lands on to `onPick` and clears the box.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import type { SystemGraph } from "@repowise-dev/types";
import { useDebounce } from "../../hooks/use-debounce";
import {
  SEARCH_MIN_CHARS,
  buildSearchIndex,
  routeHits,
  searchMap,
  type SearchHit,
  type SystemMapRouteLoader,
} from "./search-index";

const DEBOUNCE_MS = 150;

export interface MapSearch {
  query: string;
  setQuery: (q: string) => void;
  hits: SearchHit[];
  /** Index into `hits` of the highlighted result. */
  active: number;
  routesLoading: boolean;
  pick: (hit: SearchHit) => void;
  onKeyDown: (e: KeyboardEvent<HTMLInputElement>) => void;
}

/** Routes for the debounced query; stale answers are dropped, a failed load is no routes. */
function useRouteHits(graphIndex: ReturnType<typeof buildSearchIndex> | null, query: string, load?: SystemMapRouteLoader) {
  const [state, setState] = useState<{ query: string; hits: SearchHit[] }>({ query: "", hits: [] });
  const asked = useRef("");
  useEffect(() => {
    asked.current = query;
    if (!load || !graphIndex || query.length < SEARCH_MIN_CHARS) return;
    // Through a resolved promise, so a loader that throws synchronously still reads as no routes.
    Promise.resolve()
      .then(() => load(query))
      .then((links) => routeHits(links, graphIndex.resolve))
      .catch(() => [] as SearchHit[])
      .then((hits) => {
        if (asked.current === query) setState({ query, hits });
      });
  }, [load, graphIndex, query]);
  const current = state.query === query;
  return {
    hits: current ? state.hits : [],
    loading: Boolean(load) && query.length >= SEARCH_MIN_CHARS && !current,
  };
}

/** The highlighted result after an arrow key, wrapping at both ends. */
function stepIndex(current: number, step: 1 | -1, count: number): number {
  if (count === 0) return 0;
  return (Math.min(current, count - 1) + step + count) % count;
}

export function useSystemMapSearch(
  graph: SystemGraph | null,
  onPick: (hit: SearchHit) => void,
  loadRoutes?: SystemMapRouteLoader,
): MapSearch {
  const [query, setQueryState] = useState("");
  const [active, setActive] = useState(0);
  const debounced = useDebounce(query.trim(), DEBOUNCE_MS);
  const index = useMemo(() => (graph ? buildSearchIndex(graph) : null), [graph]);
  const local = useMemo(() => (index ? searchMap(index, debounced) : []), [index, debounced]);
  const routes = useRouteHits(index, debounced, loadRoutes);
  const hits = useMemo(() => [...local, ...routes.hits], [local, routes.hits]);

  const setQuery = useCallback((q: string) => {
    setQueryState(q);
    setActive(0);
  }, []);
  const pick = useCallback(
    (hit: SearchHit) => {
      setQuery("");
      onPick(hit);
    },
    [onPick, setQuery],
  );
  const settled = query.trim() === debounced;
  const onKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Escape") {
        // The first Escape clears the box, the next leaves it.
        e.preventDefault();
        if (query) setQuery("");
        else e.currentTarget.blur();
      } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        setActive((i) => stepIndex(i, e.key === "ArrowDown" ? 1 : -1, hits.length));
      } else if (e.key === "Enter") {
        e.preventDefault();
        // Results still belong to the text before the last keystroke; wait for them.
        const hit = settled ? hits[Math.min(active, hits.length - 1)] : undefined;
        if (hit) pick(hit);
      }
    },
    [hits, active, pick, query, setQuery, settled],
  );

  return {
    query,
    setQuery,
    hits,
    active: Math.min(active, Math.max(hits.length - 1, 0)),
    routesLoading: routes.loading,
    pick,
    onKeyDown,
  };
}
