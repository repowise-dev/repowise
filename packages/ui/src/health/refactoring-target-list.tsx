"use client";

import { useEffect, useMemo, useState } from "react";
import {
  HealthWorkItemCard,
  type HealthWorkItem,
  type HealthWorkItemFinding,
  type FindingStatus,
} from "./refactoring-card";

/**
 * Cards rendered per page. Matches the files view's `PAGE_SIZE`, deliberately:
 * the queue's "Load more" raises the *fetch* to `QUEUE_MAX = 500`, and every one
 * of those was previously mounted as a full `RefactoringCard` — tens of
 * thousands of DOM nodes in one commit, on top of the payload they arrived in.
 * Paging here rather than in the caller fixes it for every consumer of this
 * list at once, and it works per *group* when the queue is grouped, which a cap
 * applied to the fetched array could not do.
 */
const CARD_PAGE = 50;

export interface HealthWorkQueueListProps {
  targets: HealthWorkItem[];
  onSelect?: ((target: HealthWorkItem) => void) | undefined;
  onStatusChange?: ((findingId: string, status: FindingStatus) => void) | undefined;
  onGeneratePrompt?: ((target: HealthWorkItem) => void) | undefined;
  /** Per-card lazy fetch of a file's findings; see `RefactoringCardProps`. */
  onLoadFindings?:
    | ((filePath: string) => Promise<HealthWorkItemFinding[]>)
    | undefined;
  emptyMessage?: string;
  /** File path of the card to flash-highlight (quadrant click). */
  highlightedPath?: string | null | undefined;
}

export function HealthWorkQueueList({
  targets,
  onSelect,
  onStatusChange,
  onGeneratePrompt,
  onLoadFindings,
  emptyMessage = "No health work items match the current filters.",
  highlightedPath,
}: HealthWorkQueueListProps) {
  const [visible, setVisible] = useState(CARD_PAGE);

  // A new list is a new question — re-filtering or re-sorting must not leave
  // the reader several pages deep in results they have not seen.
  useEffect(() => {
    setVisible(CARD_PAGE);
  }, [targets]);

  // A highlighted target past the window would be invisible *and* unscrollable:
  // the quadrant highlights by file path and the card carries the only DOM
  // anchor for it, so a click on a deep dot would silently do nothing. Open
  // enough pages to include it.
  const highlightIndex = useMemo(
    () =>
      highlightedPath ? targets.findIndex((t) => t.file_path === highlightedPath) : -1,
    [targets, highlightedPath],
  );
  const shown = Math.max(
    visible,
    highlightIndex >= 0 ? Math.ceil((highlightIndex + 1) / CARD_PAGE) * CARD_PAGE : 0,
  );

  if (targets.length === 0) {
    return (
      <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-6 text-sm text-[var(--color-text-secondary)]">
        {emptyMessage}
      </div>
    );
  }
  const remaining = targets.length - shown;
  return (
    <>
      <div className="grid gap-3">
        {targets.slice(0, shown).map((t) => (
          <HealthWorkItemCard
            key={t.file_path}
            target={t}
            onSelect={onSelect}
            onStatusChange={onStatusChange}
            onGeneratePrompt={onGeneratePrompt}
            onLoadFindings={onLoadFindings}
            highlighted={highlightedPath === t.file_path}
          />
        ))}
      </div>
      {remaining > 0 ? (
        <button
          type="button"
          onClick={() => setVisible(shown + CARD_PAGE)}
          className="mt-3 w-full rounded-md border border-[var(--color-border-default)] px-3 py-2 text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
        >
          Show {Math.min(CARD_PAGE, remaining)} more ({remaining} remaining)
        </button>
      ) : null}
    </>
  );
}

export type { FindingStatus, HealthWorkItem } from "./refactoring-card";

/** @deprecated Use HealthWorkQueueList; this is a health triage queue. */
export type RefactoringTargetListProps = HealthWorkQueueListProps;
export const RefactoringTargetList = HealthWorkQueueList;
export type { RefactoringTarget } from "./refactoring-card";
