"use client";

import { Presentation } from "lucide-react";

/**
 * Header entry point for Present mode. Styled to match the docs header's
 * secondary actions (Search / Export), so it reads as one of them rather than
 * a primary call to action.
 */
export function PresentButton({ onClick, disabled }: { onClick: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title="Present this repository"
      className="flex items-center gap-2 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2.5 py-1.5 text-xs text-[var(--color-text-tertiary)] transition-colors hover:text-[var(--color-text-secondary)] disabled:pointer-events-none disabled:opacity-40"
    >
      <Presentation className="h-3.5 w-3.5" />
      <span className="hidden sm:inline">Present</span>
    </button>
  );
}
