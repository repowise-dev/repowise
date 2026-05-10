"use client";

import { useEffect, useRef, useState } from "react";
import { Maximize2, X, ZoomIn, ZoomOut, RotateCcw } from "lucide-react";

interface MermaidDiagramProps {
  chart: string;
}

let mermaidInitialized = false;

async function ensureMermaid() {
  const { default: mermaid } = await import("mermaid");
  if (!mermaidInitialized) {
    mermaid.initialize({
      startOnLoad: false,
      theme: "dark",
      themeVariables: {
        background: "#141414",
        primaryColor: "#1c1c1c",
        primaryBorderColor: "#3f3f46",
        primaryTextColor: "#e4e4e7",
        secondaryColor: "#1c1c1c",
        tertiaryColor: "#0a0a0a",
        lineColor: "#52525b",
        textColor: "#e4e4e7",
        nodeBorder: "#3f3f46",
        clusterBkg: "#0a0a0a",
        clusterBorder: "#3f3f46",
        edgeLabelBackground: "#1c1c1c",
      },
      flowchart: { htmlLabels: true, curve: "basis", padding: 12 },
      securityLevel: "loose",
    });
    mermaidInitialized = true;
  }
  return mermaid;
}

function useMermaidRender(chart: string, target: HTMLDivElement | null) {
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!target) return;
    let cancelled = false;
    setError(null);
    ensureMermaid().then((mermaid) => {
      const id = `mermaid-${Math.random().toString(36).slice(2)}`;
      mermaid
        .render(id, chart)
        .then(({ svg }) => {
          if (cancelled || !target) return;
          target.innerHTML = svg;
          const svgEl = target.querySelector("svg");
          if (svgEl) {
            svgEl.removeAttribute("width");
            svgEl.removeAttribute("height");
            svgEl.style.maxWidth = "100%";
            svgEl.style.height = "auto";
            svgEl.style.display = "block";
          }
        })
        .catch((e: unknown) => {
          if (!cancelled) setError(e instanceof Error ? e.message : "Diagram render failed");
        });
    });
    return () => {
      cancelled = true;
    };
  }, [chart, target]);

  return error;
}

function MaximizedDialog({ chart, onClose }: { chart: string; onClose: () => void }) {
  const [container, setContainer] = useState<HTMLDivElement | null>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const dragRef = useRef<{ startX: number; startY: number; panX: number; panY: number } | null>(null);
  const error = useMermaidRender(chart, container);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const onMouseDown = (e: React.MouseEvent) => {
    dragRef.current = { startX: e.clientX, startY: e.clientY, panX: pan.x, panY: pan.y };
  };
  const onMouseMove = (e: React.MouseEvent) => {
    if (!dragRef.current) return;
    setPan({
      x: dragRef.current.panX + (e.clientX - dragRef.current.startX),
      y: dragRef.current.panY + (e.clientY - dragRef.current.startY),
    });
  };
  const onMouseUp = () => {
    dragRef.current = null;
  };

  const reset = () => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Maximized diagram"
      className="fixed inset-0 z-50 flex flex-col bg-[var(--color-bg-inset)]/95 backdrop-blur-sm"
    >
      <div className="flex items-center justify-between gap-2 border-b border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-4 py-2">
        <span className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
          Diagram
        </span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setZoom((z) => Math.max(0.25, z - 0.25))}
            className="p-1.5 rounded hover:bg-[var(--color-bg-elevated)] text-[var(--color-text-secondary)]"
            aria-label="Zoom out"
          >
            <ZoomOut className="h-4 w-4" />
          </button>
          <span className="text-xs font-mono tabular-nums text-[var(--color-text-tertiary)] w-12 text-center">
            {Math.round(zoom * 100)}%
          </span>
          <button
            type="button"
            onClick={() => setZoom((z) => Math.min(4, z + 0.25))}
            className="p-1.5 rounded hover:bg-[var(--color-bg-elevated)] text-[var(--color-text-secondary)]"
            aria-label="Zoom in"
          >
            <ZoomIn className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={reset}
            className="p-1.5 rounded hover:bg-[var(--color-bg-elevated)] text-[var(--color-text-secondary)]"
            aria-label="Reset zoom"
          >
            <RotateCcw className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={onClose}
            className="ml-2 p-1.5 rounded hover:bg-[var(--color-bg-elevated)] text-[var(--color-text-secondary)]"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>
      <div
        className="flex-1 overflow-hidden cursor-grab active:cursor-grabbing"
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
      >
        {error ? (
          <p className="p-6 text-sm text-red-400">Mermaid error: {error}</p>
        ) : (
          <div
            className="w-full h-full flex items-center justify-center"
            style={{
              transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
              transformOrigin: "center center",
              transition: dragRef.current ? "none" : "transform 0.12s ease-out",
            }}
          >
            <div ref={setContainer} className="max-w-none [&_svg]:!max-w-none [&_svg]:!h-auto" />
          </div>
        )}
      </div>
    </div>
  );
}

export function MermaidDiagram({ chart }: MermaidDiagramProps) {
  const [container, setContainer] = useState<HTMLDivElement | null>(null);
  const [maximized, setMaximized] = useState(false);
  const error = useMermaidRender(chart, container);

  if (error) {
    return (
      <div className="rounded border border-[var(--color-border-default)] p-3 text-xs text-red-400">
        Mermaid error: {error}
      </div>
    );
  }

  return (
    <>
      <figure
        role="img"
        aria-label="Mermaid diagram"
        className="group relative my-4 overflow-x-auto rounded border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]"
      >
        <button
          type="button"
          onClick={() => setMaximized(true)}
          className="absolute top-2 right-2 z-10 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)]/80 backdrop-blur p-1.5 text-[var(--color-text-secondary)] opacity-0 group-hover:opacity-100 hover:text-[var(--color-text-primary)] transition-opacity"
          aria-label="Maximize diagram"
          title="Maximize"
        >
          <Maximize2 className="h-3.5 w-3.5" />
        </button>
        <div className="flex justify-center p-4">
          <div ref={setContainer} className="w-full" />
        </div>
      </figure>

      {maximized && <MaximizedDialog chart={chart} onClose={() => setMaximized(false)} />}
    </>
  );
}
