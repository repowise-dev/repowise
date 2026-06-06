"use client";

import { useEffect, useRef, useState } from "react";
import { Maximize2, X, ZoomIn, ZoomOut, RotateCcw } from "lucide-react";
import { resolveToken, useThemeVersion } from "../shared/use-theme-tokens";

interface MermaidDiagramProps {
  chart: string;
}

/**
 * Derive Mermaid's `themeVariables` from the live design tokens. Mermaid bakes
 * concrete colors into the SVG it renders, so it can't consume `var()` — we
 * read the computed token values and rebuild on each theme switch. `theme:
 * "base"` is the only built-in theme that honors a full custom palette.
 *
 * The look is the brand "blueprint" aesthetic: ink-filled rounded nodes on a
 * faint grid (grid lives on the container, see MermaidDiagram), dashed
 * connectors, and the entry node highlighted in brand orange.
 */
function mermaidThemeConfig() {
  // Rendered only client-side (dynamic import inside an effect), so the live
  // computed tokens always resolve — no SSR fallback needed.
  const elevated = resolveToken("--color-bg-elevated");
  const inset = resolveToken("--color-bg-inset");
  const text = resolveToken("--color-text-primary");
  const nodeFill = resolveToken("--color-diagram-node-fill");
  const nodeText = resolveToken("--color-diagram-node-text");
  const nodeBorder = resolveToken("--color-diagram-node-border");
  const edge = resolveToken("--color-diagram-edge");
  const clusterBorder = resolveToken("--color-diagram-cluster-border");
  const accentFill = resolveToken("--color-accent-fill");
  const textOnAccent = resolveToken("--color-text-on-accent");
  const fontMono = resolveToken("--font-mono") || "ui-monospace, Menlo, monospace";

  return {
    theme: "base" as const,
    themeVariables: {
      background: elevated,
      fontFamily: fontMono,
      fontSize: "14px",
      primaryColor: nodeFill,
      primaryBorderColor: nodeBorder,
      primaryTextColor: nodeText,
      secondaryColor: inset,
      tertiaryColor: inset,
      lineColor: edge,
      textColor: text,
      nodeBorder: nodeBorder,
      clusterBkg: "transparent",
      clusterBorder: clusterBorder,
      edgeLabelBackground: elevated,
    },
    // Flowchart-specific polish themeVariables can't express: rounded ink
    // blocks, dashed connectors, dashed cluster frames, orange entry node.
    themeCSS: `
      .node rect, .node polygon, .node circle {
        rx: 8px; ry: 8px;
        stroke-width: 1.25px;
      }
      .nodeLabel { font-weight: 600; }
      .edgePaths path, .flowchart-link {
        stroke-dasharray: 6 4;
        stroke-width: 1.5px;
      }
      .cluster rect { stroke-dasharray: 8 5; rx: 12px; ry: 12px; }
      .nodes .node:first-of-type rect,
      .nodes .node:first-of-type polygon,
      .nodes .node:first-of-type circle {
        fill: ${accentFill};
        stroke: ${accentFill};
      }
      .nodes .node:first-of-type .nodeLabel,
      .nodes .node:first-of-type .nodeLabel * {
        color: ${textOnAccent} !important;
        fill: ${textOnAccent} !important;
      }
    `,
  };
}

let mermaidSignature = "";

async function ensureMermaid() {
  const { default: mermaid } = await import("mermaid");
  const config = mermaidThemeConfig();
  const signature = JSON.stringify([config.themeVariables, config.themeCSS]);
  // Re-initialize when the resolved palette changes (i.e. on theme switch);
  // mermaid.initialize is global, so a new signature reconfigures it in place.
  if (signature !== mermaidSignature) {
    mermaid.initialize({
      startOnLoad: false,
      theme: config.theme,
      themeVariables: config.themeVariables,
      themeCSS: config.themeCSS,
      // useMaxWidth would scale wide diagrams down to the prose column,
      // shrinking node text to unreadable sizes. Render at natural size
      // instead — the inline wrapper scrolls and the dialog pans/zooms.
      flowchart: {
        htmlLabels: true,
        curve: "linear",
        padding: 16,
        nodeSpacing: 50,
        rankSpacing: 60,
        useMaxWidth: false,
      },
      sequence: { useMaxWidth: false },
      state: { useMaxWidth: false },
      class: { useMaxWidth: false },
      er: { useMaxWidth: false },
      securityLevel: "loose",
    });
    mermaidSignature = signature;
  }
  return mermaid;
}

function useMermaidRender(chart: string, target: HTMLDivElement | null) {
  const [error, setError] = useState<string | null>(null);
  // Bumped after every successful render so consumers (fit-to-viewport in the
  // dialog) can react once the SVG actually exists.
  const [renderTick, setRenderTick] = useState(0);
  const themeVersion = useThemeVersion();

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
            // Keep the natural width/height mermaid computed (useMaxWidth:
            // false). Stripping them collapses the SVG to zero width inside
            // content-sized containers — the old "blank maximize dialog" bug.
            svgEl.style.display = "block";
          }
          setRenderTick((t) => t + 1);
        })
        .catch((e: unknown) => {
          if (!cancelled) setError(e instanceof Error ? e.message : "Diagram render failed");
        });
    });
    return () => {
      cancelled = true;
    };
  }, [chart, target, themeVersion]);

  return { error, renderTick };
}

/** Natural SVG size as rendered by mermaid (transform-independent). */
function svgNaturalSize(container: HTMLDivElement | null): { w: number; h: number } | null {
  const svg = container?.querySelector("svg");
  if (!svg) return null;
  const w = parseFloat(svg.getAttribute("width") ?? "");
  const h = parseFloat(svg.getAttribute("height") ?? "");
  if (Number.isFinite(w) && Number.isFinite(h) && w > 0 && h > 0) return { w, h };
  const vb = svg.viewBox?.baseVal;
  if (vb && vb.width > 0 && vb.height > 0) return { w: vb.width, h: vb.height };
  return null;
}

const GRID_BACKGROUND: React.CSSProperties = {
  backgroundImage:
    "linear-gradient(var(--color-diagram-grid) 1px, transparent 1px), " +
    "linear-gradient(90deg, var(--color-diagram-grid) 1px, transparent 1px)",
  backgroundSize: "24px 24px",
};

function MaximizedDialog({ chart, onClose }: { chart: string; onClose: () => void }) {
  const [container, setContainer] = useState<HTMLDivElement | null>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{ startX: number; startY: number; panX: number; panY: number } | null>(null);
  const { error, renderTick } = useMermaidRender(chart, container);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Lock page scroll while the dialog is open so nothing behind it moves.
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  // Pinch/scroll zooms the *diagram*, never the page. Must be a non-passive
  // native listener — React's onWheel can't preventDefault, which is how
  // trackpad pinches used to fall through and zoom the whole browser view.
  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const factor = Math.exp(-e.deltaY * 0.002);
      setZoom((z) => Math.min(4, Math.max(0.1, z * factor)));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  // Fit-to-viewport once the SVG exists (and again after theme re-renders).
  useEffect(() => {
    if (renderTick === 0) return;
    const size = svgNaturalSize(container);
    const vp = viewportRef.current;
    if (!size || !vp) return;
    const fit = Math.min(1, (vp.clientWidth - 64) / size.w, (vp.clientHeight - 64) / size.h);
    setZoom(fit > 0 ? fit : 1);
    setPan({ x: 0, y: 0 });
  }, [renderTick, container]);

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
    const size = svgNaturalSize(container);
    const vp = viewportRef.current;
    if (size && vp) {
      const fit = Math.min(1, (vp.clientWidth - 64) / size.w, (vp.clientHeight - 64) / size.h);
      setZoom(fit > 0 ? fit : 1);
    } else {
      setZoom(1);
    }
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
            onClick={() => setZoom((z) => Math.max(0.1, z - 0.25))}
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
        ref={viewportRef}
        className="flex-1 overflow-hidden cursor-grab active:cursor-grabbing"
        style={GRID_BACKGROUND}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
      >
        {error ? (
          <p className="p-6 text-sm text-[var(--color-error)]">Mermaid error: {error}</p>
        ) : (
          <div
            className="w-full h-full flex items-center justify-center"
            style={{
              transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
              transformOrigin: "center center",
              transition: dragRef.current ? "none" : "transform 0.12s ease-out",
            }}
          >
            <div ref={setContainer} className="w-max" />
          </div>
        )}
      </div>
    </div>
  );
}

export function MermaidDiagram({ chart }: MermaidDiagramProps) {
  const [container, setContainer] = useState<HTMLDivElement | null>(null);
  const [maximized, setMaximized] = useState(false);
  const { error } = useMermaidRender(chart, container);

  if (error) {
    return (
      <div className="rounded border border-[var(--color-border-default)] p-3 text-xs text-[var(--color-error)]">
        Mermaid error: {error}
      </div>
    );
  }

  return (
    <>
      <figure
        role="img"
        aria-label="Mermaid diagram"
        className="group relative my-4 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]"
        style={GRID_BACKGROUND}
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
        {/* Natural size, centered when narrow, scrollable when wide/tall —
            never scaled down (that's what made dense diagrams unreadable). */}
        <div className="overflow-auto max-h-[75vh] p-4">
          <div ref={setContainer} className="w-max mx-auto" />
        </div>
      </figure>

      {maximized && <MaximizedDialog chart={chart} onClose={() => setMaximized(false)} />}
    </>
  );
}
