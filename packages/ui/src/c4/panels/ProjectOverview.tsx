"use client";

import { useMemo } from "react";
import { Compass, LogIn } from "lucide-react";
import { useArchitectureStore } from "../store/use-architecture-store";
import { getTone } from "../../graph-primitives/tone-styles";
import { THEME } from "../theme/theme-variables";
import { Section, Title, Sub, Pill, ActionButton } from "./panel-atoms";

export function ProjectOverview() {
  const view = useArchitectureStore((s) => s.view);
  const tourActive = useArchitectureStore((s) => s.tourActive);
  const startTour = useArchitectureStore((s) => s.startTour);
  const selectNode = useArchitectureStore((s) => s.selectNode);
  const drillIntoLayer = useArchitectureStore((s) => s.drillIntoLayer);

  const orderedLayers = useMemo(() => {
    if (!view) return [];
    return [...view.layers].sort((a, b) => a.display_order - b.display_order);
  }, [view]);

  const nodeTypeCounts = useMemo(() => {
    if (!view) return [];
    const counts = new Map<string, number>();
    for (const node of view.nodes) {
      counts.set(node.node_type, (counts.get(node.node_type) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [view]);

  const complexityCounts = useMemo(() => {
    if (!view) return { simple: 0, moderate: 0, complex: 0 };
    let simple = 0;
    let moderate = 0;
    let complex = 0;
    for (const node of view.nodes) {
      if (node.complexity === "simple") simple++;
      else if (node.complexity === "moderate") moderate++;
      else complex++;
    }
    return { simple, moderate, complex };
  }, [view]);

  const topConnected = useMemo(() => {
    if (!view) return [];
    return [...view.nodes]
      .map((n) => ({ id: n.id, name: n.name, degree: n.in_degree + n.out_degree }))
      .sort((a, b) => b.degree - a.degree)
      .slice(0, 5);
  }, [view]);

  if (!view) return null;

  const maxTypeCount = nodeTypeCounts.length > 0 ? nodeTypeCounts[0]![1] : 1;
  const totalComplexity = complexityCounts.simple + complexityCounts.moderate + complexityCounts.complex;

  return (
    <>
      {/* Orientation first (viewer plan C-1): summary, tour CTA, entry
          points, layer stack. Stats demoted below the fold. */}
      <Section>
        <Title style={{ fontSize: 15 }}>{view.project_name}</Title>
        <Sub style={{ marginTop: 4 }}>{view.project_description}</Sub>
      </Section>

      {view.tour.length > 0 && !tourActive && (
        <Section>
          <ActionButton onClick={startTour} icon={Compass} variant="primary">
            Start tour ({view.tour.length} steps)
          </ActionButton>
        </Section>
      )}

      {view.entry_points.length > 0 && (
        <Section title="Entry points">
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {view.entry_points.slice(0, 8).map((path) => {
              const name = path.split("/").pop() ?? path;
              const dir = path.slice(0, path.length - name.length);
              return (
                <button
                  key={path}
                  type="button"
                  aria-label={`Open entry point ${path}`}
                  onClick={() => selectNode(path)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    padding: "4px 6px",
                    background: "none",
                    border: "none",
                    borderRadius: 4,
                    cursor: "pointer",
                    color: "var(--color-text-primary)",
                    fontSize: 11,
                    textAlign: "left",
                  }}
                  onMouseEnter={(e) => { (e.currentTarget.style.background = "var(--color-bg-wash-hover)"); }}
                  onMouseLeave={(e) => { (e.currentTarget.style.background = "none"); }}
                >
                  <LogIn size={11} style={{ flexShrink: 0, color: "var(--color-accent-primary)" }} />
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    <span style={{ opacity: 0.5 }}>{dir}</span>
                    <span style={{ fontWeight: 600 }}>{name}</span>
                  </span>
                </button>
              );
            })}
          </div>
        </Section>
      )}

      {orderedLayers.length > 0 && (
        <Section title="Layer stack">
          <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
            {orderedLayers.map((layer) => (
              <button
                key={layer.id}
                type="button"
                aria-label={`Explore layer ${layer.name}`}
                onClick={() => drillIntoLayer(layer.id)}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  padding: "5px 8px",
                  background: "var(--color-bg-wash)",
                  border: "1px solid var(--color-border-subtle)",
                  borderRadius: 4,
                  cursor: "pointer",
                  color: "var(--color-text-primary)",
                  fontSize: 11,
                  textAlign: "left",
                }}
                onMouseEnter={(e) => { (e.currentTarget.style.background = "var(--color-bg-wash-hover)"); }}
                onMouseLeave={(e) => { (e.currentTarget.style.background = "var(--color-bg-wash)"); }}
              >
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {layer.name}
                </span>
                <span style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)", opacity: 0.55, flexShrink: 0, marginLeft: 8 }}>
                  {layer.file_count}
                </span>
              </button>
            ))}
          </div>
        </Section>
      )}

      <Section title="Stats">
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          <StatCell label="Nodes" value={String(view.total_files)} />
          <StatCell label="Edges" value={String(view.total_edges)} />
          <StatCell label="Layers" value={String(view.layers.length)} />
          <StatCell label="Languages" value={String(view.languages.length)} />
        </div>
      </Section>

      {view.languages.length > 0 && (
        <Section title="Languages">
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {view.languages.map((lang) => (
              <Pill key={lang} label={lang} />
            ))}
          </div>
        </Section>
      )}

      {view.frameworks.length > 0 && (
        <Section title="Frameworks">
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {view.frameworks.map((fw) => (
              <Pill key={fw} label={fw} />
            ))}
          </div>
        </Section>
      )}

      {nodeTypeCounts.length > 0 && (
        <Section title="Node types">
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {nodeTypeCounts.map(([type, count]) => {
              const tone = getTone(type);
              const pct = (count / maxTypeCount) * 100;
              return (
                <div key={type} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11 }}>
                  <span style={{ width: 60, opacity: 0.7, flexShrink: 0 }}>{type}</span>
                  <div style={{ flex: 1, height: 6, borderRadius: 3, background: "var(--color-bg-wash-hover)" }}>
                    <div style={{ width: `${pct}%`, height: "100%", borderRadius: 3, background: tone.band }} />
                  </div>
                  <span style={{ width: 28, textAlign: "right", fontFamily: "var(--font-mono, ui-monospace, monospace)", opacity: 0.7 }}>{count}</span>
                </div>
              );
            })}
          </div>
        </Section>
      )}

      {totalComplexity > 0 && (
        <Section title="Complexity">
          <div style={{ display: "flex", gap: 2, height: 10, borderRadius: 4, overflow: "hidden" }}>
            {complexityCounts.simple > 0 && (
              <div
                aria-label={`${complexityCounts.simple} simple`}
                style={{ flex: complexityCounts.simple, background: THEME.complexity.simple }}
              />
            )}
            {complexityCounts.moderate > 0 && (
              <div
                aria-label={`${complexityCounts.moderate} moderate`}
                style={{ flex: complexityCounts.moderate, background: THEME.complexity.moderate }}
              />
            )}
            {complexityCounts.complex > 0 && (
              <div
                aria-label={`${complexityCounts.complex} complex`}
                style={{ flex: complexityCounts.complex, background: THEME.complexity.complex }}
              />
            )}
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4, fontSize: 10, opacity: 0.6 }}>
            <span>Simple: {complexityCounts.simple}</span>
            <span>Moderate: {complexityCounts.moderate}</span>
            <span>Complex: {complexityCounts.complex}</span>
          </div>
        </Section>
      )}

      {topConnected.length > 0 && (
        <Section title="Most connected">
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {topConnected.map((n) => (
              <button
                key={n.id}
                type="button"
                aria-label={`Select ${n.name}`}
                onClick={() => selectNode(n.id)}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  padding: "4px 6px",
                  background: "none",
                  border: "none",
                  borderRadius: 4,
                  cursor: "pointer",
                  color: "var(--color-text-primary)",
                  fontSize: 11,
                  textAlign: "left",
                }}
                onMouseEnter={(e) => { (e.currentTarget.style.background = "var(--color-bg-wash-hover)"); }}
                onMouseLeave={(e) => { (e.currentTarget.style.background = "none"); }}
              >
                <span>{n.name}</span>
                <span style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)", opacity: 0.6 }}>{n.degree}</span>
              </button>
            ))}
          </div>
        </Section>
      )}

    </>
  );
}

function StatCell({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        padding: "8px 10px",
        border: "1px solid var(--color-border-subtle)",
        borderRadius: 6,
        textAlign: "center",
      }}
    >
      <div style={{ fontWeight: 700, fontSize: 16, fontFamily: "var(--font-mono, ui-monospace, monospace)" }}>{value}</div>
      <div style={{ fontSize: 10, opacity: 0.55, textTransform: "uppercase", letterSpacing: 0.4, marginTop: 2 }}>{label}</div>
    </div>
  );
}
