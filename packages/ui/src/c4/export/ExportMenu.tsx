"use client";

/**
 * Toolbar dropdown to export the current C4 view as SVG, PNG, or Mermaid.
 *
 * SVG/PNG are built locally from the React Flow layout (svg-exporter +
 * png-exporter). Mermaid is fetched from the host because the backend has
 * the authoritative C4 source — keeps the diagram view and copy-as-mermaid
 * output in sync.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Download, FileImage, FileType2, FileJson, Copy, Check } from "lucide-react";
import type { Edge, Node as FlowNode } from "@xyflow/react";
import { buildC4Svg, downloadSvg, triggerDownload } from "./svg-exporter";
import { downloadPng } from "./png-exporter";
import { exportArchitectureJson } from "./json-exporter";
import type { ArchitectureView, ArchFilters, Persona } from "../types";

export interface C4ExportMenuProps {
  nodes: FlowNode[];
  edges: Edge[];
  /** Used in the SVG title bar + as the download filename stem. */
  fileNameStem: string;
  /** Optional title rendered into the exported SVG/PNG. */
  title?: string;
  /** Host-provided fetcher for the Mermaid source. Hidden if omitted. */
  fetchMermaid?: () => Promise<string>;
  /** Disabled when the diagram is empty / still loading. */
  disabled?: boolean;
  /** Architecture view data for JSON export. JSON option hidden if omitted. */
  architectureView?: ArchitectureView;
  /** Current active filters. Required with architectureView. */
  activeFilters?: ArchFilters;
  /** Current persona. Required with architectureView. */
  activePersona?: Persona;
}

type Status = "idle" | "working" | "copied" | "error";

export function C4ExportMenu({
  nodes,
  edges,
  fileNameStem,
  title,
  fetchMermaid,
  disabled,
  architectureView,
  activeFilters,
  activePersona,
}: C4ExportMenuProps) {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<Status>("idle");
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as globalThis.Node)) setOpen(false);
    };
    window.addEventListener("mousedown", handler);
    return () => window.removeEventListener("mousedown", handler);
  }, [open]);

  const buildSvg = useCallback(
    () => buildC4Svg(nodes, edges, title ? { title } : {}),
    [nodes, edges, title],
  );

  const onSvg = useCallback(() => {
    downloadSvg(buildSvg(), `${fileNameStem}.svg`);
    setOpen(false);
  }, [buildSvg, fileNameStem]);

  const onPng = useCallback(async () => {
    setStatus("working");
    try {
      await downloadPng(buildSvg(), `${fileNameStem}.png`, { scale: 2 });
      setStatus("idle");
      setOpen(false);
    } catch {
      setStatus("error");
    }
  }, [buildSvg, fileNameStem]);

  const onMermaid = useCallback(async () => {
    if (!fetchMermaid) return;
    setStatus("working");
    try {
      const text = await fetchMermaid();
      await navigator.clipboard.writeText(text);
      setStatus("copied");
      setTimeout(() => setStatus("idle"), 1500);
    } catch {
      setStatus("error");
    }
  }, [fetchMermaid]);

  const onJson = useCallback(() => {
    if (!architectureView || !activeFilters || !activePersona) return;
    const json = exportArchitectureJson(architectureView, activeFilters, activePersona);
    triggerDownload(new Blob([json], { type: "application/json;charset=utf-8" }), `${fileNameStem}-architecture.json`);
    setOpen(false);
  }, [architectureView, activeFilters, activePersona, fileNameStem]);

  const onMermaidDownload = useCallback(async () => {
    if (!fetchMermaid) return;
    setStatus("working");
    try {
      const text = await fetchMermaid();
      triggerDownload(new Blob([text], { type: "text/plain;charset=utf-8" }), `${fileNameStem}.mmd`);
      setStatus("idle");
      setOpen(false);
    } catch {
      setStatus("error");
    }
  }, [fetchMermaid, fileNameStem]);

  return (
    <div ref={rootRef} style={{ position: "relative" }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        aria-haspopup="menu"
        aria-expanded={open}
        title="Export diagram"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          padding: "4px 10px",
          fontSize: 11,
          fontWeight: 500,
          color: "var(--color-text-secondary)",
          background: "transparent",
          border: "1px solid var(--color-border-default)",
          borderRadius: 4,
          cursor: disabled ? "not-allowed" : "pointer",
          opacity: disabled ? 0.5 : 1,
        }}
      >
        <Download size={12} />
        Export
      </button>
      {open && (
        <div
          role="menu"
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            right: 0,
            minWidth: 200,
            background: "var(--color-bg-elevated)",
            border: "1px solid var(--color-border-default)",
            borderRadius: 6,
            boxShadow: "0 10px 30px rgba(0,0,0,0.4)",
            padding: 4,
            zIndex: 10,
          }}
        >
          <MenuItem icon={<FileImage size={12} />} label="Download SVG" onClick={onSvg} />
          <MenuItem icon={<FileImage size={12} />} label="Download PNG (2×)" onClick={onPng} />
          {fetchMermaid && (
            <>
              <Divider />
              <MenuItem
                icon={status === "copied" ? <Check size={12} /> : <Copy size={12} />}
                label={status === "copied" ? "Copied" : "Copy as Mermaid"}
                onClick={onMermaid}
              />
              <MenuItem
                icon={<FileType2 size={12} />}
                label="Download Mermaid (.mmd)"
                onClick={onMermaidDownload}
              />
            </>
          )}
          {architectureView && activeFilters && activePersona && (
            <>
              <Divider />
              <MenuItem
                icon={<FileJson size={12} />}
                label="Download JSON"
                onClick={onJson}
              />
            </>
          )}
          {status === "error" && (
            <div style={{ padding: "6px 10px", fontSize: 10, color: "var(--color-error)" }}>
              Export failed. Try again.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function MenuItem({
  icon,
  label,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        width: "100%",
        padding: "6px 10px",
        background: "transparent",
        border: "none",
        color: "var(--color-text-primary)",
        fontSize: 12,
        textAlign: "left",
        cursor: "pointer",
        borderRadius: 4,
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--color-border-subtle)")}
      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
    >
      {icon}
      {label}
    </button>
  );
}

function Divider() {
  return (
    <div
      style={{
        height: 1,
        background: "var(--color-border-default)",
        margin: "4px 2px",
      }}
    />
  );
}
