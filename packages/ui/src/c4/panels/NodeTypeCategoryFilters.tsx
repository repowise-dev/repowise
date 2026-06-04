"use client";

import { useCallback } from "react";
import { useArchitectureStore } from "../store/use-architecture-store";
import type { ArchNodeType } from "../types";

const CATEGORY_NODE_TYPES: Record<string, ArchNodeType[]> = {
  code: ["file", "function", "class", "module"],
  config: ["config"],
  docs: ["document", "concept"],
  infra: ["service", "resource", "pipeline"],
  data: ["table", "endpoint", "schema"],
};

// Interim semantic mapping (warm palette); B2 replaces dots with kind icons.
const CATEGORY_COLORS: Record<string, string> = {
  code: "var(--color-accent-fill)",
  config: "var(--color-warning)",
  docs: "var(--color-info)",
  infra: "var(--color-accent-secondary)",
  data: "var(--color-success)",
};

export function NodeTypeCategoryFilters() {
  const nodeTypeFilters = useArchitectureStore((s) => s.nodeTypeFilters);
  const setNodeTypeFilter = useArchitectureStore((s) => s.setNodeTypeFilter);

  const handleToggle = useCallback(
    (category: string) => {
      const currentlyActive = nodeTypeFilters[category] !== false;
      const newValue = !currentlyActive;
      const types = CATEGORY_NODE_TYPES[category];
      if (types) {
        const state = useArchitectureStore.getState();
        const nodeTypes = new Set(state.filters.nodeTypes);
        for (const type of types) {
          if (newValue) nodeTypes.add(type); else nodeTypes.delete(type);
        }
        useArchitectureStore.setState({
          filters: { ...state.filters, nodeTypes },
          nodeTypeFilters: { ...state.nodeTypeFilters, [category]: newValue },
          containerLayoutCache: new Map(),
        });
      }
    },
    [nodeTypeFilters],
  );

  return (
    <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
      {Object.keys(CATEGORY_NODE_TYPES).map((category) => {
        const active = nodeTypeFilters[category] !== false;
        const color = CATEGORY_COLORS[category] ?? "var(--color-text-muted)";
        return (
          <button
            key={category}
            type="button"
            onClick={() => handleToggle(category)}
            style={{
              display: "inline-block",
              padding: "3px 10px",
              borderRadius: 12,
              fontSize: 10,
              fontWeight: 600,
              cursor: "pointer",
              border: `1px solid ${color}`,
              color: color,
              background: "transparent",
              opacity: active ? 1 : 0.35,
              textDecoration: active ? "none" : "line-through",
              transition: "opacity 0.15s",
            }}
          >
            {category}
          </button>
        );
      })}
    </div>
  );
}
