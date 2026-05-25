"use client";

import { useMemo } from "react";
import { Compass } from "lucide-react";
import { useArchitectureStore } from "../store/use-architecture-store";
import { getTone } from "../../graph-primitives/tone-styles";
import { Section, Title, Sub, Pill, ActionButton } from "./panel-atoms";

export function ProjectOverview() {
  const view = useArchitectureStore((s) => s.view);
  const tourActive = useArchitectureStore((s) => s.tourActive);
  const startTour = useArchitectureStore((s) => s.startTour);
  const selectNode = useArchitectureStore((s) => s.selectNode);

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
      <Section>
        <Title style={{ fontSize: 15 }}>{view.project_name}</Title>
        <Sub style={{ marginTop: 4 }}>{view.project_description}</Sub>
      </Section>

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
                  <div style={{ flex: 1, height: 6, borderRadius: 3, background: "rgba(148,163,184,0.1)" }}>
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
                style={{ flex: complexityCounts.simple, background: "#22c55e" }}
              />
            )}
            {complexityCounts.moderate > 0 && (
              <div
                aria-label={`${complexityCounts.moderate} moderate`}
                style={{ flex: complexityCounts.moderate, background: "#f59e0b" }}
              />
            )}
            {complexityCounts.complex > 0 && (
              <div
                aria-label={`${complexityCounts.complex} complex`}
                style={{ flex: complexityCounts.complex, background: "#ef4444" }}
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
                  color: "var(--color-text-primary, #f1f5f9)",
                  fontSize: 11,
                  textAlign: "left",
                }}
                onMouseEnter={(e) => { (e.currentTarget.style.background = "rgba(148,163,184,0.1)"); }}
                onMouseLeave={(e) => { (e.currentTarget.style.background = "none"); }}
              >
                <span>{n.name}</span>
                <span style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)", opacity: 0.6 }}>{n.degree}</span>
              </button>
            ))}
          </div>
        </Section>
      )}

      {view.tour.length > 0 && !tourActive && (
        <Section>
          <ActionButton onClick={startTour} icon={Compass}>
            Start Guided Tour ({view.tour.length} steps)
          </ActionButton>
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
        border: "1px solid rgba(148,163,184,0.12)",
        borderRadius: 6,
        textAlign: "center",
      }}
    >
      <div style={{ fontWeight: 700, fontSize: 16, fontFamily: "var(--font-mono, ui-monospace, monospace)" }}>{value}</div>
      <div style={{ fontSize: 10, opacity: 0.55, textTransform: "uppercase", letterSpacing: 0.4, marginTop: 2 }}>{label}</div>
    </div>
  );
}
