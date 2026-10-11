"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { DeckView } from "./deck-view";
import { usePresentKeyboard } from "./use-present-keyboard";
import type { PresentModel } from "./types";

interface PresentOverlayProps {
  model: PresentModel;
  onClose: () => void;
  /** Jump to a page in the reader (host closes the overlay + navigates). */
  onOpenPage?: (pageId: string) => void;
}

/**
 * Full-screen, keyboard-driven presentation surface. Escapes the dashboard
 * chrome entirely (fixed inset-0), locks page scroll, and is theme-aware via
 * CSS tokens only. Slides sit on the base plane; the bar above is chrome.
 */
export function PresentOverlay({ model, onClose, onOpenPage }: PresentOverlayProps) {
  const [index, setIndex] = useState(0);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const total = model.slides.length;

  const goto = useCallback((i: number) => setIndex(Math.min(total - 1, Math.max(0, i))), [total]);

  usePresentKeyboard(dialogRef, {
    onPrev: () => goto(index - 1),
    onNext: () => goto(index + 1),
    onFirst: () => goto(0),
    onLast: () => goto(total - 1),
    onClose,
  });

  // Lock page scroll and take focus for the overlay's lifetime; hand focus
  // back to whatever opened it on close.
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialogRef.current?.focus();
    return () => {
      document.body.style.overflow = prev;
      opener?.focus();
    };
  }, []);

  return (
    <div
      ref={dialogRef}
      tabIndex={-1}
      role="dialog"
      aria-modal="true"
      aria-label={`${model.repoName} presentation`}
      className="fixed inset-0 z-[var(--z-modal)] flex flex-col bg-[var(--color-bg-root)] outline-none"
    >
      <header className="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-4">
        <span className="min-w-0 truncate text-[12px] font-medium text-[var(--color-text-secondary)]">
          {model.repoName}
        </span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close presentation (Esc)"
          title="Close (Esc)"
          className="rounded-md p-1.5 text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
        >
          <X className="h-4 w-4" />
        </button>
      </header>

      <div className="min-h-0 flex-1">
        <DeckView slides={model.slides} index={index} onIndex={goto} onOpenPage={onOpenPage} />
      </div>
    </div>
  );
}
