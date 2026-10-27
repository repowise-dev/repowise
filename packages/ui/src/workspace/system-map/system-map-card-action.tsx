"use client";

/**
 * Shared bits of the aggregate cards (group, tray, "+N more"): the card's one
 * verb as a quiet accent link, and the invisible handles connectors attach to.
 */

import type { CSSProperties } from "react";

/** Connectors attach here; the handle itself is never shown or grabbed. */
export const HIDDEN_HANDLE: CSSProperties = { opacity: 0, pointerEvents: "none" };

/** The card's one verb. Out of the tab order: Enter on the card does the same. */
export function CardAction({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      tabIndex={-1}
      className="nodrag nopan"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      onDoubleClick={(e) => e.stopPropagation()}
      style={{
        flexShrink: 0,
        border: 0,
        background: "transparent",
        padding: 0,
        cursor: "pointer",
        fontSize: 12,
        lineHeight: "16px",
        fontWeight: 500,
        color: "var(--color-accent-primary)",
      }}
    >
      {label}
    </button>
  );
}
