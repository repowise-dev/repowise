"use client";

import { useCallback, useEffect, useState } from "react";
import { X, Presentation, Route } from "lucide-react";
import { cn } from "../lib/cn";
import { DeckView } from "./deck-view";
import { WalkthroughView } from "./walkthrough-view";
import { usePresentKeyboard } from "./use-present-keyboard";
import type { PresentModel } from "./types";

export type PresentMode = "deck" | "walkthrough";

interface PresentOverlayProps {
  model: PresentModel;
  initialMode?: PresentMode;
  onClose: () => void;
  onModeChange?: (mode: PresentMode) => void;
  /** Jump to a page in the reader (host closes the overlay + navigates). */
  onOpenPage?: (pageId: string) => void;
}

const MODES: { value: PresentMode; label: string; icon: typeof Presentation }[] = [
  { value: "deck", label: "Deck", icon: Presentation },
  { value: "walkthrough", label: "Walkthrough", icon: Route },
];

/**
 * Full-screen, keyboard-driven presentation surface. Escapes the dashboard
 * chrome entirely (fixed inset-0), locks page scroll, and is theme-aware via
 * CSS tokens only. Holds a separate index per mode so toggling Deck /
 * Walkthrough preserves each one's position.
 */
export function PresentOverlay({ model, initialMode = "deck", onClose, onModeChange, onOpenPage }: PresentOverlayProps) {
  const [mode, setMode] = useState<PresentMode>(initialMode);
  const [deckIndex, setDeckIndex] = useState(0);
  const [stepIndex, setStepIndex] = useState(0);

  const hasWalkthrough = model.walkthrough.length > 0;
  const total = mode === "deck" ? model.deck.length : model.walkthrough.length;
  const index = mode === "deck" ? deckIndex : stepIndex;
  const setIndex = mode === "deck" ? setDeckIndex : setStepIndex;

  const clamp = useCallback((i: number) => Math.min(total - 1, Math.max(0, i)), [total]);
  const goto = useCallback((i: number) => setIndex(clamp(i)), [setIndex, clamp]);

  const switchMode = useCallback(
    (next: PresentMode) => {
      setMode(next);
      onModeChange?.(next);
    },
    [onModeChange],
  );

  usePresentKeyboard({
    onPrev: () => goto(index - 1),
    onNext: () => goto(index + 1),
    onFirst: () => goto(0),
    onLast: () => goto(total - 1),
    onClose,
  });

  // Lock page scroll for the overlay's lifetime.
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Repository presentation"
      className="fixed inset-0 z-[var(--z-modal)] flex flex-col bg-[var(--color-bg-inset)]"
    >
      {/* Top bar */}
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-[var(--color-border-default)] bg-[var(--color-bg-root)]/80 px-4 backdrop-blur">
        <div className="flex items-center rounded-lg bg-[var(--color-bg-elevated)] p-0.5" role="tablist" aria-label="Presentation mode">
          {MODES.map((m) => {
            const Icon = m.icon;
            const disabled = m.value === "walkthrough" && !hasWalkthrough;
            const active = mode === m.value;
            return (
              <button
                key={m.value}
                type="button"
                role="tab"
                aria-selected={active}
                disabled={disabled}
                onClick={() => switchMode(m.value)}
                className={cn(
                  "flex items-center gap-1.5 rounded-md px-3 py-1 text-xs font-medium transition-colors disabled:opacity-40",
                  active
                    ? "bg-[var(--color-bg-surface)] text-[var(--color-text-primary)] shadow-sm"
                    : "text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]",
                )}
              >
                <Icon className="h-3.5 w-3.5" />
                {m.label}
              </button>
            );
          })}
        </div>

        <span className="pointer-events-none absolute left-1/2 hidden -translate-x-1/2 text-xs font-medium text-[var(--color-text-tertiary)] sm:block">
          {model.repoName}
        </span>

        <button
          type="button"
          onClick={onClose}
          aria-label="Close presentation (Esc)"
          title="Close (Esc)"
          className="rounded-md p-1.5 text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
        >
          <X className="h-4 w-4" />
        </button>
      </header>

      {/* Body */}
      <div className="min-h-0 flex-1">
        {mode === "deck" ? (
          <DeckView slides={model.deck} index={deckIndex} onIndex={goto} onOpenPage={onOpenPage} />
        ) : (
          <WalkthroughView
            steps={model.walkthrough}
            index={stepIndex}
            onIndex={goto}
            totalMinutes={model.totalMinutes}
            onOpenPage={onOpenPage}
          />
        )}
      </div>
    </div>
  );
}
