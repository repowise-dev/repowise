"use client";

/**
 * Decoder ring for the blueprint KG canvas (kg-ux plan B6) — what the ink
 * weights, signal glyphs, edges, and gestures mean. Collapsible chip so it
 * never competes with the canvas; rendered on every tier.
 */

import { useState } from "react";
import { BookOpen, ChevronDown, ChevronUp, Flame, Play, Skull } from "lucide-react";

const SWATCH_BASE: React.CSSProperties = {
  width: 14,
  height: 14,
  borderRadius: 4,
  flexShrink: 0,
  display: "inline-block",
};

function Row({ swatch, children }: { swatch: React.ReactNode; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 10.5 }}>
      {swatch}
      <span style={{ color: "var(--color-text-secondary)" }}>{children}</span>
    </div>
  );
}

export function ArchLegend() {
  const [open, setOpen] = useState(false);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "flex-start",
        gap: 6,
        fontFamily: "var(--font-sans, system-ui, sans-serif)",
      }}
    >
      {open && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 7,
            padding: "10px 12px",
            borderRadius: 10,
            background: "var(--color-bg-elevated)",
            border: "1px solid var(--color-border-default)",
            boxShadow: "0 6px 20px rgba(0,0,0,0.18)",
            maxWidth: 240,
          }}
        >
          <Row swatch={<span style={{ ...SWATCH_BASE, backgroundImage: "var(--gradient-ember)" }} />}>
            Start here — entry points
          </Row>
          <Row swatch={<span style={{ ...SWATCH_BASE, background: "var(--color-kg-node-fill)", border: "1px solid var(--color-kg-node-border)" }} />}>
            This scope
          </Row>
          <Row swatch={<span style={{ ...SWATCH_BASE, background: "var(--color-kg-node-fill-2)", border: "1px solid var(--color-kg-node-border-2)" }} />}>
            Supporting — siblings, tests, barrels
          </Row>
          <Row swatch={<span style={{ ...SWATCH_BASE, background: "transparent", border: "1.5px dashed var(--color-diagram-cluster-border)" }} />}>
            Boundary / pointer out of scope
          </Row>

          <div style={{ height: 1, background: "var(--color-border-subtle)", margin: "2px 0" }} />

          <Row
            swatch={
              <span style={{ display: "inline-flex", gap: 3, color: "var(--color-text-secondary)" }}>
                <Play size={11} aria-hidden />
                <Flame size={11} aria-hidden />
                <Skull size={11} aria-hidden />
                <BookOpen size={11} aria-hidden />
              </span>
            }
          >
            Entry · hotspot · dead code · has docs
          </Row>
          <Row
            swatch={
              <svg width="22" height="8" aria-hidden style={{ flexShrink: 0 }}>
                <line x1="0" y1="4" x2="22" y2="4" stroke="var(--color-diagram-edge)" strokeWidth="1.5" strokeDasharray="5 3" />
              </svg>
            }
          >
            Relation — the chip glyph tells the kind
          </Row>

          <div style={{ height: 1, background: "var(--color-border-subtle)", margin: "2px 0" }} />

          <div
            style={{
              fontFamily: "var(--font-mono, ui-monospace, monospace)",
              fontSize: 9.5,
              color: "var(--color-text-secondary)",
              lineHeight: 1.6,
            }}
          >
            Click = inspect · 2×click = open
            <br />
            Esc = back · F = filters · / = search
          </div>
        </div>
      )}

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label={open ? "Collapse legend" : "Expand legend"}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 5,
          padding: "4px 10px",
          borderRadius: 999,
          fontSize: 10.5,
          fontWeight: 600,
          cursor: "pointer",
          background: "var(--color-bg-elevated)",
          border: "1px solid var(--color-border-default)",
          color: "var(--color-text-secondary)",
        }}
      >
        {open ? <ChevronDown size={11} aria-hidden /> : <ChevronUp size={11} aria-hidden />}
        Legend
      </button>
    </div>
  );
}
