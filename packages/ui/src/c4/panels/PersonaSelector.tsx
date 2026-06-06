"use client";

import { useArchitectureStore } from "../store/use-architecture-store";
import type { Persona } from "../types";

export function PersonaSelector() {
  const persona = useArchitectureStore((s) => s.persona);
  const setPersona = useArchitectureStore((s) => s.setPersona);

  return (
    <select
      value={persona}
      onChange={(e) => setPersona(e.target.value as Persona)}
      style={{
        background: "transparent",
        border: "1px solid var(--color-border-default)",
        borderRadius: 4,
        padding: "4px 8px",
        fontSize: 11,
        color: "var(--color-text-primary)",
        cursor: "pointer",
        outline: "none",
      }}
    >
      <option value="overview">Overview</option>
      <option value="learn">Learn</option>
      <option value="deep-dive">Deep Dive</option>
    </select>
  );
}
