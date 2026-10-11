"use client";

import { useArchitectureStore } from "../store/use-architecture-store";
import type { Persona } from "../types";

export function PersonaSelector() {
  const persona = useArchitectureStore((s) => s.persona);
  const setPersona = useArchitectureStore((s) => s.setPersona);

  return (
    <label
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        fontSize: 10,
        color: "var(--color-text-tertiary)",
        textTransform: "uppercase",
        letterSpacing: "0.05em",
        fontWeight: 500,
      }}
    >
      Lens
      <select
        value={persona}
        onChange={(e) => setPersona(e.target.value as Persona)}
        aria-label="Reading lens: how much detail the view shows"
        title="How much detail the view shows: a quick overview, a guided learning pass, or everything"
        style={{
          background: "transparent",
          border: "1px solid var(--color-border-default)",
          borderRadius: 4,
          padding: "4px 8px",
          fontSize: 11,
          color: "var(--color-text-primary)",
          cursor: "pointer",
          outline: "none",
          textTransform: "none",
          letterSpacing: "normal",
          fontWeight: 400,
        }}
      >
        <option value="overview">Overview</option>
        <option value="learn">Learn</option>
        <option value="deep-dive">Deep Dive</option>
      </select>
    </label>
  );
}
