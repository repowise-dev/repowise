"use client";

import { useArchitectureStore } from "../store/use-architecture-store";

/** Tier breadcrumb for the architecture view: Overview → Layer → Group.
 * Stays consistent whether or not a layer has a sub-group tier (decision 1).
 */
export function ArchBreadcrumb() {
  const view = useArchitectureStore((s) => s.view);
  const navigationLevel = useArchitectureStore((s) => s.navigationLevel);
  const activeLayerId = useArchitectureStore((s) => s.activeLayerId);
  const activeSubGroupId = useArchitectureStore((s) => s.activeSubGroupId);

  if (!view || navigationLevel === "overview") return null;

  const layer = view.layers.find((l) => l.id === activeLayerId);
  const subGroup = layer?.sub_groups.find((g) => g.id === activeSubGroupId);

  const crumbStyle: React.CSSProperties = {
    background: "none",
    border: "none",
    padding: 0,
    font: "inherit",
    cursor: "pointer",
    color: "var(--color-text-secondary)",
  };
  const sep = <span style={{ opacity: 0.5 }}>›</span>;
  const current: React.CSSProperties = {
    color: "var(--color-text-primary)",
    fontWeight: 600,
  };

  const goOverview = () => {
    const store = useArchitectureStore.getState();
    // Two-stage drillOut: jump straight to the overview from any tier.
    store.drillOut();
    if (useArchitectureStore.getState().navigationLevel !== "overview") {
      store.drillOut();
    }
  };

  const goLayerGroups = () => {
    if (activeLayerId) useArchitectureStore.getState().drillIntoLayer(activeLayerId);
  };

  return (
    <nav
      aria-label="Architecture navigation"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        fontSize: 12,
      }}
    >
      <button type="button" style={crumbStyle} onClick={goOverview}>
        Overview
      </button>
      {layer && (
        <>
          {sep}
          {navigationLevel === "layer-detail" && activeSubGroupId ? (
            <button type="button" style={crumbStyle} onClick={goLayerGroups}>
              {layer.name}
            </button>
          ) : (
            <span style={current}>{layer.name}</span>
          )}
        </>
      )}
      {activeSubGroupId && (
        <>
          {sep}
          <span style={current}>
            {subGroup?.name ?? activeSubGroupId.split(":").pop()}
          </span>
        </>
      )}
    </nav>
  );
}
