"use client";

/**
 * Static legend for the Live System Map: what the edge colours/glyphs mean
 * (by kind), what the dash patterns mean (by match confidence), and the node
 * health scale. Reads the same registries the map renders from, so the two can
 * never drift.
 */

import { EDGE_KIND_ORDER, SYSTEM_EDGE_KINDS } from "./edge-kinds";

function Row({ children }: { children: React.ReactNode }) {
  return <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>{children}</div>;
}

function Item({ swatch, label }: { swatch: React.ReactNode; label: string }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 10.5, color: "var(--color-text-secondary)" }}>
      {swatch}
      {label}
    </span>
  );
}

function dashLine(dash: string, label: string) {
  return (
    <Item
      key={label}
      label={label}
      swatch={
        <svg width={22} height={6} aria-hidden>
          <line x1={0} y1={3} x2={22} y2={3} stroke="var(--color-text-secondary)" strokeWidth={1.5} strokeDasharray={dash} />
        </svg>
      }
    />
  );
}

export function SystemMapLegend() {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 6,
        padding: "8px 10px",
        background: "var(--color-bg-surface)",
        border: "1px solid var(--color-border-default)",
        borderRadius: 8,
        maxWidth: 460,
      }}
    >
      <Row>
        {EDGE_KIND_ORDER.map((kind) => {
          const s = SYSTEM_EDGE_KINDS[kind];
          const Icon = s.icon;
          return <Item key={kind} label={s.label} swatch={<Icon size={11} style={{ color: s.color }} aria-hidden />} />;
        })}
      </Row>
      <Row>
        {dashLine("none", "Exact / manual")}
        {dashLine("6 4", "Candidate")}
        {dashLine("2 4", "Inferred (co-change)")}
      </Row>
      <Row>
        <span style={{ fontSize: 10.5, color: "var(--color-text-tertiary)" }}>Node ring = repo health:</span>
        <Item swatch={<Dot color="var(--color-risk-low)" />} label="healthy" />
        <Item swatch={<Dot color="var(--color-risk-medium)" />} label="moderate" />
        <Item swatch={<Dot color="var(--color-risk-high)" />} label="at risk" />
      </Row>
    </div>
  );
}

function Dot({ color }: { color: string }) {
  return <span style={{ width: 10, height: 10, borderRadius: "50%", background: color, display: "inline-block" }} />;
}
