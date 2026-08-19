"use client";

/**
 * Key for the Live System Map: what the edge colours/glyphs mean (by kind), what
 * the dash patterns mean (by match confidence), and the node health scale. Reads
 * the same registries the map renders from, so the two can never drift.
 *
 * Rendered as a hairline caption row under the canvas, not as a card on it — a
 * key is a caption, and the diagram is the thing the reader came for.
 */

import { EDGE_KIND_ORDER, SYSTEM_EDGE_KINDS } from "./edge-kinds";

function Item({ swatch, label }: { swatch: React.ReactNode; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[10.5px] text-[var(--color-text-secondary)]">
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

function Dot({ color }: { color: string }) {
  return <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: color }} />;
}

export function SystemMapLegend() {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-[var(--color-border-subtle)] px-1 pt-2">
      <div className="flex flex-wrap items-center gap-2.5">
        {EDGE_KIND_ORDER.map((kind) => {
          const s = SYSTEM_EDGE_KINDS[kind];
          const Icon = s.icon;
          return <Item key={kind} label={s.label} swatch={<Icon size={11} style={{ color: s.color }} aria-hidden />} />;
        })}
      </div>
      <div className="flex flex-wrap items-center gap-2.5">
        {dashLine("none", "Exact / manual")}
        {dashLine("6 4", "Candidate")}
        {dashLine("2 4", "Inferred (co-change)")}
      </div>
      <div className="flex flex-wrap items-center gap-2.5">
        <span className="text-[10.5px] text-[var(--color-text-tertiary)]">Node ring = repo health:</span>
        <Item swatch={<Dot color="var(--color-risk-low)" />} label="healthy" />
        <Item swatch={<Dot color="var(--color-risk-medium)" />} label="moderate" />
        <Item swatch={<Dot color="var(--color-risk-high)" />} label="at risk" />
      </div>
    </div>
  );
}
