"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import type { RefactoringOpportunity } from "@repowise-dev/types/refactoring";
import {
  HealthWorkItemCard,
  type HealthWorkItem,
  type HealthWorkItemFinding,
  type FindingStatus,
} from "./refactoring-card";

/**
 * Cards mounted per page. The queue now pages server-side at the same size, so
 * this is inert for that caller and stays as the guard for any consumer that
 * hands over a longer array: every target used to mount as a full card, which
 * was tens of thousands of DOM nodes in one commit.
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
  /** Resolve a file's composed refactoring opportunity, on first expand. */
  onLoadOpportunity?:
    | ((filePath: string) => Promise<RefactoringOpportunity | null>)
    | undefined;
  refactoringOpportunityHref?: ((opportunityId: string) => string) | undefined;
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
  onLoadOpportunity,
  refactoringOpportunityHref,
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

  // Arrow keys walk the queue. Each card's path button is the row's focus
  // anchor, so this moves between files without stealing Tab from the controls
  // inside an expanded card.
  const gridRef = useRef<HTMLDivElement | null>(null);
  const onArrowKey = useCallback((e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const grid = gridRef.current;
    if (!grid) return;
    const headers = [...grid.querySelectorAll<HTMLButtonElement>("[data-work-item-header]")];
    const here = headers.findIndex((h) => h.contains(e.target as Node));
    if (here === -1) return;
    // Swallowed at either end too: an arrow that scrolls the page away from
    // the list reads as the key having done nothing.
    e.preventDefault();
    headers[here + (e.key === "ArrowDown" ? 1 : -1)]?.focus();
  }, []);

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
      <div className="grid gap-3" onKeyDown={onArrowKey} ref={gridRef}>
        {targets.slice(0, shown).map((t) => (
          <HealthWorkItemCard
            key={t.file_path}
            target={t}
            onSelect={onSelect}
            onStatusChange={onStatusChange}
            onGeneratePrompt={onGeneratePrompt}
            onLoadFindings={onLoadFindings}
            onLoadOpportunity={onLoadOpportunity}
            refactoringOpportunityHref={refactoringOpportunityHref}
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
