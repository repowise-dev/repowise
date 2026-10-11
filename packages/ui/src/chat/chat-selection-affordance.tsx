"use client";

import { MessageCircleQuestion } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { ChatContext, ChatSelection } from "@repowise-dev/types/chat";
import { useChatHandoff } from "./chat-handoff";

/** A region whose text can be asked about. Optional companion attributes:
 *  `data-chat-selection-path` names the file, and `data-line` on any element
 *  inside gives the control a line range. */
export const CHAT_SELECTION_ATTRIBUTE = "data-chat-selection";
const PATH_ATTRIBUTE = "data-chat-selection-path";

/** Cleared bottom-right so the control never lands on the compact chat dock.
 *  The expanded dock needs no zone: the control renders beneath its stacking
 *  level, so a selection made behind it cannot paint over it. */
const DOCK_ZONE_WIDTH = 460;
const DOCK_ZONE_HEIGHT = 200;
const VIEWPORT_MARGIN = 8;
const GAP_ABOVE_SELECTION = 8;

interface Placement {
  selection: ChatSelection;
  top: number;
  left: number;
}

function elementOf(node: Node | null): Element | null {
  if (!node) return null;
  return node.nodeType === Node.ELEMENT_NODE
    ? (node as Element)
    : node.parentElement;
}

/**
 * The first and last numbered lines the selection actually covers.
 *
 * Asks the range which line elements it intersects rather than reading the
 * boundary containers: a boundary normalizes to a parent element on a
 * triple-click or a click at the start of a line, and reading that parent
 * would name the region's first and last line whatever the reader picked.
 * A wrong range is worse than no range.
 */
function lineRange(
  range: Range,
  region: HTMLElement,
): { startLine?: number | undefined; endLine?: number | undefined } {
  const covered: number[] = [];
  for (const element of region.querySelectorAll("[data-line]")) {
    if (!range.intersectsNode(element)) continue;
    const line = Number(element.getAttribute("data-line"));
    if (Number.isFinite(line)) covered.push(line);
  }
  if (covered.length === 0) return {};
  return { startLine: covered[0], endLine: covered[covered.length - 1] };
}

/** The selectable region a range sits in, or null when it spans two of them. */
function regionOf(range: Range): HTMLElement | null {
  const start = elementOf(range.startContainer)?.closest<HTMLElement>(
    `[${CHAT_SELECTION_ATTRIBUTE}]`,
  );
  if (!start) return null;
  const end = elementOf(range.endContainer)?.closest<HTMLElement>(
    `[${CHAT_SELECTION_ATTRIBUTE}]`,
  );
  return end === start ? start : null;
}

function readPlacement(): Placement | null {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) return null;
  const text = selection.toString().trim();
  if (!text) return null;

  const range = selection.getRangeAt(0);
  const region = regionOf(range);
  if (!region) return null;

  const rect = range.getClientRects()[0] ?? range.getBoundingClientRect();
  if (!rect || (rect.width === 0 && rect.height === 0)) return null;

  const path = region.getAttribute(PATH_ATTRIBUTE) ?? undefined;
  const { startLine, endLine } = lineRange(range, region);

  return {
    selection: {
      text,
      ...(path ? { path } : {}),
      ...(startLine === undefined ? {} : { startLine }),
      ...(endLine === undefined ? {} : { endLine }),
    },
    top: rect.top,
    left: rect.left,
  };
}

export interface ChatSelectionAffordanceProps {
  /** Host-owned mapping from the selected file to the chat context. */
  resolveContext: (path?: string) => ChatContext;
  question?: string;
  label?: string;
}

/**
 * A control that appears beside a text selection inside a region marked with
 * `data-chat-selection`, and nowhere else.
 *
 * Mounted once per host. It renders nothing at rest, so it adds no permanent
 * chrome to any page.
 */
export function ChatSelectionAffordance({
  resolveContext,
  question = "Explain this selection.",
  label = "Ask about selection",
}: ChatSelectionAffordanceProps) {
  const { request, selectionEnabled } = useChatHandoff();
  const [placement, setPlacement] = useState<Placement | null>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  const sync = useCallback(() => setPlacement(readPlacement()), []);

  /** Follows the page without re-reading the selection. Scrolling moves the
   *  control but cannot change which lines are covered, and rescanning them
   *  would walk every numbered line in a long file on every scroll event. */
  const reposition = useCallback(() => {
    setPlacement((current) => {
      if (!current) return current;
      const selection = window.getSelection();
      if (!selection || selection.isCollapsed || selection.rangeCount === 0) {
        return null;
      }
      const range = selection.getRangeAt(0);
      const rect = range.getClientRects()[0] ?? range.getBoundingClientRect();
      if (!rect) return current;
      return { ...current, top: rect.top, left: rect.left };
    });
  }, []);

  useEffect(() => {
    if (!selectionEnabled) {
      setPlacement(null);
      return undefined;
    }
    // `selectionchange` alone fires mid-drag on every character, which would
    // make the control chase the pointer. Settle on pointer and key release.
    const clear = () => {
      const selection = window.getSelection();
      if (!selection || selection.isCollapsed) setPlacement(null);
    };
    document.addEventListener("selectionchange", clear);
    document.addEventListener("mouseup", sync);
    document.addEventListener("keyup", sync);
    window.addEventListener("scroll", reposition, true);
    window.addEventListener("resize", reposition);
    return () => {
      document.removeEventListener("selectionchange", clear);
      document.removeEventListener("mouseup", sync);
      document.removeEventListener("keyup", sync);
      window.removeEventListener("scroll", reposition, true);
      window.removeEventListener("resize", reposition);
    };
  }, [reposition, selectionEnabled, sync]);

  // Clamp after measuring: the control's width depends on its label, and the
  // bottom-right clearance depends on that width. Runs before paint, so the
  // first placement is never seen. It only ever moves the control, never hides
  // it: a focusable control nobody can see is worse than one placed roughly.
  useLayoutEffect(() => {
    const button = buttonRef.current;
    if (!button || !placement) return;
    const { width, height } = button.getBoundingClientRect();

    let top = placement.top - height - GAP_ABOVE_SELECTION;
    if (top < VIEWPORT_MARGIN) top = placement.top + GAP_ABOVE_SELECTION;
    let left = Math.max(
      VIEWPORT_MARGIN,
      Math.min(placement.left, window.innerWidth - width - VIEWPORT_MARGIN),
    );
    const inDockZone =
      top + height > window.innerHeight - DOCK_ZONE_HEIGHT &&
      left + width > window.innerWidth - DOCK_ZONE_WIDTH;
    if (inDockZone) {
      left = Math.max(VIEWPORT_MARGIN, window.innerWidth - DOCK_ZONE_WIDTH - width);
    }
    button.style.top = `${top}px`;
    button.style.left = `${left}px`;
  }, [placement]);

  if (!selectionEnabled || !placement) return null;

  return (
    <button
      ref={buttonRef}
      type="button"
      // Keep the selection alive: a plain mousedown on the control would
      // collapse it before the click handler could read it.
      onMouseDown={(event) => event.preventDefault()}
      onClick={() => {
        request({
          context: resolveContext(placement.selection.path),
          question,
          selection: placement.selection,
        });
        setPlacement(null);
      }}
      style={{ top: placement.top, left: placement.left }}
      className="fixed z-[calc(var(--z-modal)-1)] inline-flex h-8 items-center gap-1.5 whitespace-nowrap rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] px-2.5 text-xs text-[var(--color-text-secondary)] shadow-[var(--shadow-md)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
    >
      <MessageCircleQuestion className="h-3.5 w-3.5" aria-hidden />
      {label}
    </button>
  );
}
