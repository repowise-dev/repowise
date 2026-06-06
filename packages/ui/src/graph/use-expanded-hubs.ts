"use client";

import { useState, useCallback } from "react";

/**
 * Expand/collapse state for constellation hubs (the radial Knowledge Graph
 * blossom). Multiple hubs may be expanded at once; the order is tracked
 * most-recent-LAST so Esc / {@link collapseLast} closes the most recently
 * opened cluster (documented behavior). {@link collapseAll} closes everything.
 *
 * Pure client state — the actual member slices are fetched separately
 * (see useCommunitySlices) and merged into the sigma graph by the shell.
 */
export function useExpandedHubs() {
  // Insertion-ordered list of expanded community ids (oldest → newest).
  const [expandedHubs, setExpandedHubs] = useState<number[]>([]);

  const toggleHub = useCallback((communityId: number) => {
    setExpandedHubs((prev) =>
      prev.includes(communityId)
        ? prev.filter((id) => id !== communityId)
        : [...prev, communityId],
    );
  }, []);

  const expandHub = useCallback((communityId: number) => {
    setExpandedHubs((prev) =>
      prev.includes(communityId) ? prev : [...prev, communityId],
    );
  }, []);

  const collapseHub = useCallback((communityId: number) => {
    setExpandedHubs((prev) => prev.filter((id) => id !== communityId));
  }, []);

  // Esc semantics: collapse the most recently expanded hub. Returns true when
  // something was collapsed, so callers can decide whether to also clear other
  // state (selection, ego, etc.).
  const collapseLast = useCallback((): boolean => {
    let collapsed = false;
    setExpandedHubs((prev) => {
      if (prev.length === 0) return prev;
      collapsed = true;
      return prev.slice(0, -1);
    });
    return collapsed;
  }, []);

  const collapseAll = useCallback(() => setExpandedHubs([]), []);

  return {
    expandedHubs,
    toggleHub,
    expandHub,
    collapseHub,
    collapseLast,
    collapseAll,
  };
}
