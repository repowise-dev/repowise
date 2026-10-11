"use client";

import { X } from "lucide-react";
import type { C4NodeData } from "../types";

export interface C4NodeInspectorProps {
  data: C4NodeData | null;
  onClose: () => void;
  onDrillIn?: ((containerId: string) => void) | undefined;
}

export function C4NodeInspector({ data, onClose, onDrillIn }: C4NodeInspectorProps) {
  if (!data) return null;
  return (
    <aside
      aria-label="Node details"
      style={{
        position: "absolute",
        top: 12,
        right: 12,
        width: 280,
        maxHeight: "70%",
        overflow: "auto",
        padding: 12,
        background: "var(--color-bg-elevated, rgba(17,24,39,0.95))",
        border: "1px solid var(--color-border-default)",
        borderRadius: 8,
        color: "var(--color-text-primary)",
        fontSize: 12,
        zIndex: 5,
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 8,
        }}
      >
        <strong style={{ textTransform: "uppercase", fontSize: 10, letterSpacing: 0.6, opacity: 0.7 }}>
          {data.kind}
        </strong>
        <button
          type="button"
          aria-label="Close inspector"
          onClick={onClose}
          style={{ background: "none", border: "none", cursor: "pointer", color: "inherit" }}
        >
          <X size={14} />
        </button>
      </header>
      <InspectorBody data={data} onDrillIn={onDrillIn} />
    </aside>
  );
}

function InspectorBody({
  data,
  onDrillIn,
}: {
  data: C4NodeData;
  onDrillIn?: ((containerId: string) => void) | undefined;
}) {
  switch (data.kind) {
    case "system":
      return <Row title={data.system.name} sub={data.system.description} />;
    case "person":
      return <Row title={data.person.name} sub={data.person.description} />;
    case "external": {
      const e = data.external;
      return (
        <>
          <Row title={e.display_name || e.name} sub={e.ecosystem} />
          <KV k="category" v={e.category} />
          {e.version && <KV k="version" v={e.version} />}
          <KV k="name" v={e.name} />
        </>
      );
    }
    case "container": {
      const c = data.container;
      return (
        <>
          <Row title={c.name} sub={c.path} />
          <KV k="language" v={c.language || "—"} />
          <KV k="files" v={String(c.file_count)} />
          <KV k="symbols" v={String(c.symbol_count)} />
          {c.hotspot_count > 0 && <KV k="hotspots" v={String(c.hotspot_count)} />}
          {c.dead_count > 0 && <KV k="dead" v={String(c.dead_count)} />}
          {onDrillIn && (
            <button
              type="button"
              onClick={() => onDrillIn(c.id)}
              style={{
                marginTop: 10,
                padding: "5px 10px",
                background: "var(--color-accent-muted, rgba(245,149,32,0.2))",
                color: "var(--color-accent-primary)",
                border: "1px solid var(--color-accent-primary)",
                borderRadius: 4,
                cursor: "pointer",
                fontSize: 11,
                fontWeight: 500,
              }}
            >
              Drill into components →
            </button>
          )}
        </>
      );
    }
    case "component": {
      const c = data.component;
      return (
        <>
          <Row title={c.name === "_root" ? "(root)" : c.name} sub={c.path} />
          <KV k="container" v={c.container_id} />
          <KV k="files" v={String(c.file_count)} />
          <KV k="symbols" v={String(c.symbol_count)} />
        </>
      );
    }
  }
}

function Row({ title, sub }: { title: string; sub?: string }) {
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ fontWeight: 600, fontSize: 13 }}>{title}</div>
      {sub && <div style={{ opacity: 0.7, fontSize: 11, marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

function KV({ k, v }: { k: string; v: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "3px 0", borderTop: "1px solid var(--color-bg-wash-hover)" }}>
      <span style={{ opacity: 0.6 }}>{k}</span>
      <span style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)" }}>{v}</span>
    </div>
  );
}
