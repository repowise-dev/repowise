"use client";

import { useCallback } from "react";
import { useArchitectureStore } from "../store/use-architecture-store";
import { getKindIcon } from "../nodes/kind-icons";
import type { ArchNodeType } from "../types";

const CATEGORY_NODE_TYPES: Record<string, ArchNodeType[]> = {
  code: ["file", "function", "class", "module"],
  config: ["config"],
  docs: ["document", "concept"],
  infra: ["service", "resource", "pipeline"],
  data: ["table", "endpoint", "schema"],
};

// Each category pill carries its representative kind glyph (ink system —
// icons encode type, not color; kg-ux plan §2.2).
const CATEGORY_ICON_KIND: Record<string, string> = {
  code: "file",
  config: "config",
  docs: "document",
  infra: "service",
  data: "table",
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
        const Icon = getKindIcon(CATEGORY_ICON_KIND[category] ?? "file");
        return (
          <button
            key={category}
            type="button"
            onClick={() => handleToggle(category)}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              padding: "3px 10px",
              borderRadius: 12,
              fontSize: 10,
              fontWeight: 600,
              cursor: "pointer",
              border: active
                ? "1px solid var(--color-accent-primary)"
                : "1px solid var(--color-border-default)",
              color: active ? "var(--color-accent-primary)" : "var(--color-text-secondary)",
              background: active ? "var(--color-accent-muted)" : "transparent",
              opacity: active ? 1 : 0.55,
              textDecoration: active ? "none" : "line-through",
              transition: "opacity 0.15s",
            }}
          >
            <Icon size={11} aria-hidden />
            {category}
          </button>
        );
      })}
    </div>
  );
}
