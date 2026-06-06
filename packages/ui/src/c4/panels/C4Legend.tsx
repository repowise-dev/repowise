"use client";

import { getTone } from "../../graph-primitives/tone-styles";

// Swatches come from the same tone palette the nodes render with, so the
// legend can never drift from the canvas.
const ITEMS: { label: string; color: string }[] = [
  { label: "System",    color: getTone("system").band },
  { label: "Person",    color: getTone("person").band },
  { label: "Container", color: getTone("container").band },
  { label: "Component", color: getTone("component").band },
  { label: "External",  color: getTone("external").band },
];

export function C4Legend() {
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: 10,
        padding: "6px 10px",
        fontSize: 11,
        color: "var(--color-text-secondary)",
        background: "var(--color-bg-surface, rgba(15,23,42,0.7))",
        border: "1px solid var(--color-border-default)",
        borderRadius: 6,
      }}
    >
      {ITEMS.map((it) => (
        <span key={it.label} style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
          <span
            aria-hidden
            style={{
              width: 10,
              height: 10,
              borderRadius: 2,
              background: it.color,
              border: "1px solid var(--color-border-default)",
            }}
          />
          {it.label}
        </span>
      ))}
    </div>
  );
}
