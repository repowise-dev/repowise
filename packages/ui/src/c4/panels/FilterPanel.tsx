"use client";

import { useMemo } from "react";
import { X } from "lucide-react";
import { useArchitectureStore } from "../store/use-architecture-store";
import { Section, ActionButton } from "./panel-atoms";

function CheckboxRow({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        padding: "4px 0",
        fontSize: 11,
        cursor: "pointer",
        color: "var(--color-text-primary)",
      }}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        style={{ accentColor: "var(--color-accent-primary)" }}
      />
      {label}
    </label>
  );
}

export function FilterPanel() {
  const view = useArchitectureStore((s) => s.view);
  const filters = useArchitectureStore((s) => s.filters);
  const filterPanelOpen = useArchitectureStore((s) => s.filterPanelOpen);
  const setNodeTypeFilter = useArchitectureStore((s) => s.setNodeTypeFilter);
  const setComplexityFilter = useArchitectureStore((s) => s.setComplexityFilter);
  const setLayerFilter = useArchitectureStore((s) => s.setLayerFilter);
  const setEdgeCategoryFilter = useArchitectureStore((s) => s.setEdgeCategoryFilter);
  const resetFilters = useArchitectureStore((s) => s.resetFilters);
  const setFilterPanelOpen = useArchitectureStore((s) => s.setFilterPanelOpen);
  const showTests = useArchitectureStore((s) => s.showTests);
  const setShowTests = useArchitectureStore((s) => s.setShowTests);
  const hasTestLayer = useArchitectureStore(
    (s) => s.view?.layers.some((l) => l.id === "layer:test") ?? false,
  );

  const nodeTypes = useMemo(() => {
    if (!view) return [];
    const types = new Set<string>();
    for (const node of view.nodes) {
      types.add(node.node_type);
    }
    return [...types].sort();
  }, [view]);

  const edgeCategories = useMemo(() => {
    if (!view) return [];
    const cats = new Set<string>();
    for (const edge of view.edges) {
      cats.add(edge.edge_type);
    }
    return [...cats].sort();
  }, [view]);

  if (!filterPanelOpen || !view) return null;

  return (
    <div
      style={{
        position: "absolute",
        top: 48,
        left: 12,
        width: 280,
        background: "var(--color-bg-elevated, rgba(17,24,39,0.96))",
        border: "1px solid var(--color-border-default)",
        borderRadius: 8,
        boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
        zIndex: 8,
        maxHeight: "calc(100% - 72px)",
        overflowY: "auto",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "10px 12px",
          borderBottom: "1px solid var(--color-border-subtle)",
        }}
      >
        <span style={{ fontSize: 12, fontWeight: 600, color: "var(--color-text-primary)" }}>
          Filters
        </span>
        <button
          type="button"
          onClick={() => setFilterPanelOpen(false)}
          style={{
            background: "transparent",
            border: "none",
            cursor: "pointer",
            color: "var(--color-text-secondary)",
            padding: 2,
          }}
        >
          <X size={14} />
        </button>
      </div>

      <Section title="Node Types">
        {nodeTypes.map((type) => (
          <CheckboxRow
            key={type}
            label={type}
            checked={filters.nodeTypes.has(type)}
            onChange={(checked) => setNodeTypeFilter(type, checked)}
          />
        ))}
      </Section>

      <Section title="Complexity">
        {["simple", "moderate", "complex"].map((c) => (
          <CheckboxRow
            key={c}
            label={c}
            checked={filters.complexities.has(c)}
            onChange={(checked) => setComplexityFilter(c, checked)}
          />
        ))}
      </Section>

      {hasTestLayer && (
        <Section title="Display">
          {/* Tests mirror the code — demoted by default (decision 2). */}
          <CheckboxRow
            label="Show tests"
            checked={showTests}
            onChange={setShowTests}
          />
        </Section>
      )}

      <Section title="Layers">
        {view.layers.map((layer) => (
          <CheckboxRow
            key={layer.id}
            label={layer.name}
            checked={filters.layerIds.has(layer.id)}
            onChange={(checked) => setLayerFilter(layer.id, checked)}
          />
        ))}
      </Section>

      <Section title="Edge Categories">
        {edgeCategories.map((cat) => (
          <CheckboxRow
            key={cat}
            label={cat}
            checked={filters.edgeCategories.has(cat)}
            onChange={(checked) => setEdgeCategoryFilter(cat, checked)}
          />
        ))}
      </Section>

      <div style={{ padding: "10px 12px" }}>
        <ActionButton onClick={resetFilters} variant="ghost">
          Reset All
        </ActionButton>
      </div>
    </div>
  );
}
