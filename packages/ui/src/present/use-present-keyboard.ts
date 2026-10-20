"use client";

import { useEffect, type RefObject } from "react";

interface PresentKeys {
  onPrev: () => void;
  onNext: () => void;
  onClose: () => void;
  onFirst: () => void;
  onLast: () => void;
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])';

/** Keep Tab inside `dialog`, wrapping at either end. */
function trapTab(e: KeyboardEvent, dialog: HTMLElement) {
  const items = [...dialog.querySelectorAll<HTMLElement>(FOCUSABLE)];
  const first = items[0];
  const last = items[items.length - 1];
  if (!first || !last) {
    e.preventDefault();
    dialog.focus();
    return;
  }
  const active = document.activeElement;
  const inside = active instanceof Node && dialog.contains(active) && active !== dialog;
  if (!inside || (e.shiftKey && active === first) || (!e.shiftKey && active === last)) {
    e.preventDefault();
    (e.shiftKey ? last : first).focus();
  }
}

/**
 * Keyboard for the Present dialog: Left/PageUp = prev, Right/PageDown/Space =
 * next, Home/End = first/last, Escape = close, Tab stays inside. Ignores keys
 * while a text field is focused, and Space on a button so it still presses it.
 *
 * A modal opened from a slide (a maximized diagram) owns every key while it is
 * mounted, so the deck neither moves nor closes underneath it.
 */
export function usePresentKeyboard(
  dialogRef: RefObject<HTMLElement | null>,
  { onPrev, onNext, onClose, onFirst, onLast }: PresentKeys,
) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const dialog = dialogRef.current;
      if (!dialog) return;
      const nested = [...document.querySelectorAll('[role="dialog"][aria-modal="true"]')].some(
        (el) => el !== dialog,
      );
      if (nested) return;
      if (e.key === "Tab") {
        trapTab(e, dialog);
        return;
      }
      const target = e.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) {
        return;
      }
      if (e.key === " " && target?.closest?.("button, a")) return;
      const action: Record<string, () => void> = {
        ArrowRight: onNext,
        PageDown: onNext,
        " ": onNext,
        ArrowLeft: onPrev,
        PageUp: onPrev,
        Home: onFirst,
        End: onLast,
        Escape: onClose,
      };
      const run = action[e.key];
      if (!run) return;
      e.preventDefault();
      run();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dialogRef, onPrev, onNext, onClose, onFirst, onLast]);
}
