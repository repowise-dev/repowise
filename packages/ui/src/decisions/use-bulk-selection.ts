"use client";

import * as React from "react";
import type { DecisionRecord } from "@repowise-dev/types/decisions";
import type { BulkReviewResult } from "./bulk-review";

/** Row selection for a table, as the column renderer reads it. */
export interface Selection {
  selected: ReadonlySet<string>;
  toggle: (id: string) => void;
  allSelected: boolean;
  toggleAll: () => void;
  busy: boolean;
}

export interface BulkState {
  total: number;
  settled: number;
  /** Null while the run is in flight. */
  result: BulkReviewResult | null;
}

type DismissMany = (
  decisions: DecisionRecord[],
  onProgress: (settled: number) => void,
) => Promise<BulkReviewResult>;

/**
 * Selection over the rows on screen and one bulk dismiss run over it.
 *
 * A new page is a different set of records, so ids that left the rows drop
 * out of the selection; a new lane clears it and orphans any run still in
 * flight, so that run cannot write its outcome into a lane the reader left.
 */
export function useBulkSelection(
  rows: DecisionRecord[],
  lane: string,
  dismissMany: DismissMany | undefined,
) {
  const [selected, setSelected] = React.useState<ReadonlySet<string>>(new Set());
  const [bulk, setBulk] = React.useState<BulkState | null>(null);
  const runRef = React.useRef(0);
  // The rows as they are when a run settles, which may be a later page than
  // the one it started on.
  const rowsRef = React.useRef(rows);
  rowsRef.current = rows;

  const rowIds = rows.map((d) => d.id).join(",");
  React.useEffect(() => {
    setSelected((prev) => {
      const visible = new Set(rowIds.split(","));
      const kept = [...prev].filter((id) => visible.has(id));
      return kept.length === prev.size ? prev : new Set(kept);
    });
  }, [rowIds]);
  React.useEffect(() => {
    runRef.current += 1;
    setSelected(new Set());
    setBulk(null);
  }, [lane]);

  const allSelected = rows.length > 0 && rows.every((d) => selected.has(d.id));
  const selection: Selection = {
    selected,
    allSelected,
    busy: bulk !== null && bulk.result === null,
    toggle: (id) =>
      setSelected((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
      }),
    toggleAll: () => setSelected(allSelected ? new Set() : new Set(rows.map((d) => d.id))),
  };

  const dismissSelected = async () => {
    const picked = rows.filter((d) => selected.has(d.id));
    if (!dismissMany || picked.length === 0) return;
    const run = runRef.current;
    setBulk({ total: picked.length, settled: 0, result: null });
    const result = await settle(dismissMany, picked, (settled) => {
      if (runRef.current === run) setBulk((b) => (b ? { ...b, settled } : b));
    });
    if (runRef.current !== run) return;
    setBulk({ total: picked.length, settled: picked.length, result });
    // What was refused stays selected, so a retry is one click, but only
    // where it is still on screen: a reader who paged on meanwhile would
    // otherwise get a count and a button for rows they cannot see.
    const visible = new Set(rowsRef.current.map((d) => d.id));
    setSelected(new Set(result.failed.map((f) => f.decision.id).filter((id) => visible.has(id))));
  };

  const clear = () => {
    setSelected(new Set());
    setBulk(null);
  };

  return { selected, bulk, selection, dismissSelected, clear };
}

/**
 * A host that throws instead of reporting per record still has to leave the
 * bar usable, so the whole selection then reads as refused.
 */
async function settle(
  dismissMany: DismissMany,
  picked: DecisionRecord[],
  onProgress: (settled: number) => void,
): Promise<BulkReviewResult> {
  try {
    return await dismissMany(picked, onProgress);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return { done: [], failed: picked.map((decision) => ({ decision, message })) };
  }
}
