"use client";

/**
 * A one-time coaching hint that floats over the canvas on a first visit. The
 * zoom-map interaction model (scroll to zoom, double-click a card to dive in) is
 * not obvious from a static frame, so we spell it out once and remember the
 * dismissal in localStorage. Deliberately calm: a single glass pill, no modal
 * and no backdrop, so it guides without getting in the way.
 */

import { useEffect, useState } from "react";
import { Move, MousePointerClick, X, ZoomIn } from "lucide-react";

const DISMISS_KEY = "repowise:zoom-hint-dismissed";

export function ZoomHint() {
  // Start hidden so SSR and the pre-hydration paint never flash the hint; reveal
  // it only after we have checked localStorage on the client.
  const [show, setShow] = useState(false);

  useEffect(() => {
    try {
      if (localStorage.getItem(DISMISS_KEY) !== "1") setShow(true);
    } catch {
      // Private mode / storage disabled: still guide the user, just don't persist.
      setShow(true);
    }
  }, []);

  if (!show) return null;

  const dismiss = () => {
    setShow(false);
    try {
      localStorage.setItem(DISMISS_KEY, "1");
    } catch {
      // Non-fatal: the hint simply reappears next time.
    }
  };

  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-4 z-10 flex justify-center px-3">
      <div className="pointer-events-auto flex max-w-full items-center gap-3 overflow-x-auto rounded-full border border-[var(--color-border-subtle)] bg-[var(--color-bg-glass)] py-1.5 pl-4 pr-2 text-xs text-[var(--color-text-secondary)] shadow-sm backdrop-blur">
        <span className="flex shrink-0 items-center gap-1.5">
          <ZoomIn className="h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
          Scroll to zoom
        </span>
        <span className="text-[var(--color-border-default)]">·</span>
        <span className="flex shrink-0 items-center gap-1.5">
          <Move className="h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
          Drag to pan
        </span>
        <span className="text-[var(--color-border-default)]">·</span>
        <span className="flex shrink-0 items-center gap-1.5">
          <MousePointerClick className="h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
          Double-click a card to dive in
        </span>
        <button
          type="button"
          onClick={dismiss}
          aria-label="Dismiss hint"
          className="ml-1 shrink-0 rounded-full p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}
