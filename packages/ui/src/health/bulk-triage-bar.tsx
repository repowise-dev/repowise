"use client";

import { useState } from "react";
import type { FindingStatus } from "./refactoring-card";

/**
 * Status writes in flight at once. There is no batch route, so a bulk action
 * is one PATCH per finding; this keeps a large selection from opening dozens
 * of requests against a local server. Ceiling: a selection of hundreds takes
 * hundreds of round trips; a server batch endpoint is the upgrade.
 */
export const BULK_CONCURRENCY = 4;

/** Run *fn* over *items* with at most *cap* in flight; report which failed. */
export async function runCapped<T>(
  items: readonly T[],
  cap: number,
  fn: (item: T) => Promise<unknown>,
): Promise<{ ok: T[]; failed: T[] }> {
  const ok: T[] = [];
  const failed: T[] = [];
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const item = items[next++] as T;
      try {
        await fn(item);
        ok.push(item);
      } catch {
        failed.push(item);
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(cap, items.length) }, worker));
  return { ok, failed };
}

const ACTIONS: { status: FindingStatus; label: string }[] = [
  { status: "acknowledged", label: "Acknowledge" },
  { status: "resolved", label: "Resolved" },
  { status: "false_positive", label: "False positive" },
];

export interface BulkTriageBarProps {
  /** Selected rows: file path to the id of the finding the row names. */
  selection: ReadonlyMap<string, string>;
  /** Rows on this page that can be selected, for "Select all on this page". */
  selectablePaths: readonly { path: string; findingId: string }[];
  onSelectAll: () => void;
  onClear: () => void;
  updateStatus: (findingId: string, status: FindingStatus) => Promise<unknown>;
  /** After a run: the paths whose write failed stay selected. */
  onDone: (failedPaths: string[]) => void;
}

/**
 * The bulk triage bar, shown while any row is selected. Each row stands for
 * the finding it names, so the bar sets that finding's status, as the row's
 * own buttons would.
 */
export function BulkTriageBar({
  selection,
  selectablePaths,
  onSelectAll,
  onClear,
  updateStatus,
  onDone,
}: BulkTriageBarProps) {
  const [running, setRunning] = useState<FindingStatus | null>(null);
  const [failedCount, setFailedCount] = useState(0);

  if (selection.size === 0 && failedCount === 0) return null;

  const run = async (status: FindingStatus) => {
    setRunning(status);
    setFailedCount(0);
    const rows = [...selection.entries()];
    const { failed } = await runCapped(rows, BULK_CONCURRENCY, ([, id]) => updateStatus(id, status));
    setRunning(null);
    setFailedCount(failed.length);
    onDone(failed.map(([path]) => path));
  };

  const n = selection.size;
  const allOnPage = selectablePaths.every((r) => selection.has(r.path));

  return (
    <div
      role="region"
      aria-label="Bulk triage"
      className="sticky bottom-0 max-sm:bottom-20 z-10 -mx-1 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] px-3 py-2 text-xs shadow-md"
    >
      <span className="min-w-0 text-[var(--color-text-secondary)]">
        <span className="font-mono tabular-nums text-[var(--color-text-primary)]">{n}</span>{" "}
        {n === 1 ? "row" : "rows"} selected. Sets the status of the finding each row names.
        {failedCount > 0 ? (
          <span role="status" className="ml-1 text-[var(--color-error)]">
            {failedCount} could not be updated and {failedCount === 1 ? "stays" : "stay"} selected.
          </span>
        ) : null}
      </span>
      {/* Actions sit beside the count, not at the far edge, and the bar rides
          higher on phones: floating page chrome (a chat launcher) sits in the
          bottom corner. */}
      <div className="flex flex-wrap items-center gap-1.5">
        {ACTIONS.map((a) => (
          <button
            key={a.status}
            type="button"
            disabled={running != null || n === 0}
            onClick={() => void run(a.status)}
            className="rounded-md border border-[var(--color-border-default)] px-2 py-1 font-medium text-[var(--color-text-primary)] hover:border-[var(--color-border-hover)] hover:bg-[var(--color-bg-elevated)] disabled:opacity-50"
          >
            {running === a.status ? "Updating…" : a.label}
          </button>
        ))}
        {!allOnPage ? (
          <button
            type="button"
            onClick={onSelectAll}
            disabled={running != null}
            className="px-1.5 py-1 text-[var(--color-text-secondary)] underline decoration-dotted underline-offset-2 hover:text-[var(--color-text-primary)] disabled:opacity-50"
          >
            Select all on this page
          </button>
        ) : null}
        <button
          type="button"
          onClick={() => {
            setFailedCount(0);
            onClear();
          }}
          disabled={running != null}
          className="px-1.5 py-1 text-[var(--color-text-secondary)] underline decoration-dotted underline-offset-2 hover:text-[var(--color-text-primary)] disabled:opacity-50"
        >
          Clear
        </button>
      </div>
    </div>
  );
}
