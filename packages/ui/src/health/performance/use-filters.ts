"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  INITIAL_FILTERS,
  parseLegacyFilters,
  readFilters,
  writeFilters,
  type PerformanceFilterState,
} from "./query";

/** A host string is the flat parameters, or a link minted before them. */
export function initialState(initial: string | undefined): PerformanceFilterState {
  if (!initial) return INITIAL_FILTERS;
  const params = new URLSearchParams(initial);
  // A host that predates the flat schema hands over the blob itself.
  return params.has("context") && !params.has("ctx") && !params.has("scope")
    ? parseLegacyFilters(initial)
    : readFilters(params);
}

/** The flat parameters a state writes; equal strings mean the same view. */
function encode(state: PerformanceFilterState): string {
  const params = new URLSearchParams();
  writeFilters(state, params);
  return params.toString();
}

/**
 * The tab's filter state, seeded from the host's URL string and kept in step
 * with it: a URL the view did not write itself (a link followed while the tab
 * stays mounted, back or forward) replaces the state.
 *
 * `current` is read through a ref so two changes in one tick compose, and so
 * callbacks handed to memoized cards need not change with every filter.
 */
export function usePerformanceFilters(
  initialFilters: string | undefined,
  onFiltersChange: ((search: string, state: PerformanceFilterState) => void) | undefined,
) {
  const [filters, setFilters] = useState(() => initialState(initialFilters));
  const current = useRef(filters);
  current.current = filters;
  // What this view handed the host and has not yet seen come back, oldest
  // first, so an intermediate URL from two quick changes is not read as a jump.
  const written = useRef<string[]>([]);

  const apply = useCallback(
    (next: PerformanceFilterState) => {
      current.current = next;
      setFilters(next);
      if (onFiltersChange) {
        const search = encode(next);
        written.current.push(search);
        onFiltersChange(search, next);
      }
    },
    [onFiltersChange],
  );

  useEffect(() => {
    const incoming = initialState(initialFilters);
    const key = encode(incoming);
    const echo = written.current.indexOf(key);
    if (echo >= 0) {
      written.current.splice(0, echo + 1);
      return;
    }
    if (key === encode(current.current)) return;
    written.current = [];
    current.current = incoming;
    setFilters(incoming);
  }, [initialFilters]);

  return { filters, current, apply };
}
