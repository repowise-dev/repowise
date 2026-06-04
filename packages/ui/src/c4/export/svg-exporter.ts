/**
 * Build a clean, standalone SVG string from the C4 React Flow nodes + edges.
 *
 * We don't screenshot the DOM — we re-render the same layout positions into
 * native SVG primitives. That keeps the file small, vector-perfect, and
 * independent of whatever fonts or CSS happen to be on the page.
 */

import type { Edge, Node } from "@xyflow/react";
import type { C4EdgeData, C4NodeData } from "../types";
import { TONE_STYLES, type ToneName } from "../../graph-primitives/tone-styles";
import { resolveToken } from "../../shared/use-theme-tokens";

/**
 * Blueprint ink palette, resolved from the LIVE theme at export time so the
 * file matches what the user sees (kg-ux plan B7). The literals are
 * light-mode fallbacks for headless export (jsdom/tests) where computed
 * styles are empty.
 */
function resolveInkPalette() {
  return {
    canvas: resolveToken("--color-bg-canvas", "#f4eae1"),
    grid: resolveToken("--color-diagram-grid", "rgba(245,149,32,0.10)"),
    ink: resolveToken("--color-kg-node-fill", "#fffdf8"),
    ink2: resolveToken("--color-kg-node-fill-2", "#f3ece2"),
    inkText: resolveToken("--color-kg-node-text", "#241b2c"),
    inkBorder: resolveToken("--color-kg-node-border", "#241b2c"),
    edge: resolveToken("--color-diagram-edge", "#4a3d59"),
    cluster: resolveToken("--color-diagram-cluster-border", "#826aa0"),
    accent: resolveToken("--color-accent-fill", "#f59520"),
    accentEnd: "#f7a94d", // --gradient-ember end stop
    textOnAccent: resolveToken("--color-text-on-accent", "#241b2c"),
    paper: resolveToken("--color-bg-surface", "#ffffff"),
    textPrimary: resolveToken("--color-text-primary", "#241b2c"),
    textSecondary: resolveToken("--color-text-secondary", "#5e5360"),
    success: resolveToken("--color-success", "#1d8155"),
    warning: resolveToken("--color-warning", "#9a6614"),
    error: resolveToken("--color-error", "#b23a2e"),
  };
}
type InkPalette = ReturnType<typeof resolveInkPalette>;

interface ArchFileNodeData {
  node: {
    node_type: string;
    name: string;
    summary: string;
    complexity: string;
    language: string | null;
    in_degree: number;
    out_degree: number;
    is_entry_point: boolean;
    is_hotspot: boolean;
    is_dead: boolean;
    has_doc: boolean;
  };
}

interface LayerClusterNodeData {
  layer: {
    name: string;
    description: string;
    file_count: number;
    health_score: number | null;
  };
}

interface ArchContainerNodeData {
  containerId: string;
  label: string;
  childCount: number;
  expanded: boolean;
}

interface PortalNodeData {
  targetLayerId: string;
  targetLayerName: string;
  edgeCount: number;
}


const PADDING = 40;
const BAND_HEIGHT = 18;
const FONT_FAMILY = "system-ui, -apple-system, Segoe UI, sans-serif";
const MONO_FONT = "ui-monospace, Menlo, Consolas, monospace";

function escapeXml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function nodeTone(data: C4NodeData): ToneName {
  return data.kind;
}

function nodeTitle(data: C4NodeData): string {
  switch (data.kind) {
    case "system":    return data.system.name;
    case "person":    return data.person.name;
    case "external":  return data.external.display_name || data.external.name;
    case "container": return data.container.name === "_root" ? "(root)" : data.container.name;
    case "component": return data.component.name === "_root" ? "(root)" : data.component.name;
  }
}

function nodeSubtitle(data: C4NodeData): string | null {
  switch (data.kind) {
    case "system":    return data.system.description || null;
    case "person":    return data.person.description || null;
    case "external":  return `${data.external.ecosystem} · ${data.external.category}`;
    case "container": return data.container.path;
    case "component": return data.component.path;
  }
}

function nodeFooter(data: C4NodeData): string | null {
  if (data.kind === "container") {
    return `${data.container.file_count} files · ${data.container.symbol_count} symbols`;
  }
  if (data.kind === "component") {
    return `${data.component.file_count} files · ${data.component.symbol_count} symbols`;
  }
  return null;
}

function truncate(text: string, max: number): string {
  return text.length <= max ? text : text.slice(0, Math.max(0, max - 1)) + "…";
}

function nodeKindLabel(data: C4NodeData): string {
  return data.kind.toUpperCase();
}

interface Bounds {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

function computeBounds(nodes: Node[]): Bounds {
  if (nodes.length === 0) {
    return { minX: 0, minY: 0, maxX: 800, maxY: 600 };
  }
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const n of nodes) {
    const w = (n.width as number | undefined) ?? 200;
    const h = (n.height as number | undefined) ?? 100;
    minX = Math.min(minX, n.position.x);
    minY = Math.min(minY, n.position.y);
    maxX = Math.max(maxX, n.position.x + w);
    maxY = Math.max(maxY, n.position.y + h);
  }
  return { minX, minY, maxX, maxY };
}

function renderNode(n: Node): string {
  const data = n.data as unknown as C4NodeData;
  const tone = nodeTone(data);
  const style = TONE_STYLES[tone];
  const w = (n.width as number | undefined) ?? 200;
  const h = (n.height as number | undefined) ?? 100;
  const x = n.position.x;
  const y = n.position.y;

  const title = escapeXml(truncate(nodeTitle(data), Math.floor(w / 8)));
  const subtitle = nodeSubtitle(data);
  const footer = nodeFooter(data);
  const kind = escapeXml(nodeKindLabel(data));

  const parts: string[] = [];
  parts.push(`<g transform="translate(${x},${y})">`);
  // box
  parts.push(
    `<rect width="${w}" height="${h}" rx="8" ry="8" fill="${style.bg}" stroke="${style.border}" stroke-width="1.5"/>`,
  );
  // header band
  parts.push(
    `<rect width="${w}" height="${BAND_HEIGHT}" rx="8" ry="8" fill="${style.band}"/>`,
  );
  // squared bottom of band
  parts.push(
    `<rect y="${BAND_HEIGHT - 8}" width="${w}" height="8" fill="${style.band}"/>`,
  );
  parts.push(
    `<text x="8" y="${BAND_HEIGHT - 5}" font-family="${FONT_FAMILY}" font-size="9" font-weight="600" letter-spacing="0.6" fill="${style.text}" opacity="0.85">${kind}</text>`,
  );
  // title
  parts.push(
    `<text x="10" y="${BAND_HEIGHT + 20}" font-family="${FONT_FAMILY}" font-size="13" font-weight="600" fill="${style.text}">${title}</text>`,
  );
  if (subtitle) {
    parts.push(
      `<text x="10" y="${BAND_HEIGHT + 36}" font-family="${FONT_FAMILY}" font-size="11" fill="${style.text}" opacity="0.75">${escapeXml(truncate(subtitle, Math.floor(w / 6)))}</text>`,
    );
  }
  if (footer) {
    parts.push(
      `<text x="10" y="${h - 8}" font-family="${FONT_FAMILY}" font-size="10" fill="${style.text}" opacity="0.8">${escapeXml(truncate(footer, Math.floor(w / 6)))}</text>`,
    );
  }
  parts.push(`</g>`);
  return parts.join("");
}

function renderArchNode(n: Node, pal: InkPalette): string {
  const type = n.type ?? "";
  const w = (n.width as number | undefined) ?? 200;
  const h = (n.height as number | undefined) ?? 100;
  const x = n.position.x;
  const y = n.position.y;
  const parts: string[] = [];

  // Shared ink-block scaffolding (mirrors InkNodeShell anatomy).
  const inkBox = (fill: string, dashed = false, gradient = false) =>
    `<rect width="${w}" height="${h}" rx="12" ry="12" fill="${gradient ? "url(#kg-ember)" : fill}" stroke="${dashed ? pal.cluster : pal.inkBorder}" stroke-width="1.5"${dashed ? ' stroke-dasharray="8 5"' : ""}/>`;

  if (type === "scopeFrame") {
    const data = n.data as unknown as { label: string };
    parts.push(`<g transform="translate(${x},${y})">`);
    parts.push(`<rect width="${w}" height="${h}" rx="16" ry="16" fill="none" stroke="${pal.cluster}" stroke-width="1.5" stroke-dasharray="8 5"/>`);
    parts.push(`<rect x="20" y="-9" width="${Math.min(w - 40, data.label.length * 7 + 20)}" height="18" rx="6" fill="${pal.canvas}" stroke="${pal.cluster}" stroke-width="1" stroke-dasharray="4 3"/>`);
    parts.push(`<text x="30" y="4" font-family="${MONO_FONT}" font-size="9" font-weight="600" letter-spacing="1" fill="${pal.textSecondary}">${escapeXml(data.label.toUpperCase())}</text>`);
    parts.push(`</g>`);
  } else if (type === "archFile") {
    const data = n.data as unknown as ArchFileNodeData;
    const nodeData = data.node;
    const isEntry = nodeData.is_entry_point;
    const text = isEntry ? pal.textOnAccent : pal.inkText;
    const kindLabel = nodeData.language
      ? `${nodeData.node_type.toUpperCase()} · ${nodeData.language}`
      : nodeData.node_type.toUpperCase();
    const title = escapeXml(truncate(nodeData.name, Math.floor(w / 8)));
    const subtitle = escapeXml(truncate(nodeData.summary, Math.floor(w / 6)));
    const complexityColor =
      nodeData.complexity === "simple" ? pal.success : nodeData.complexity === "moderate" ? pal.warning : pal.error;

    parts.push(`<g transform="translate(${x},${y})">`);
    parts.push(inkBox(pal.ink, false, isEntry));
    parts.push(`<text x="12" y="22" font-family="${MONO_FONT}" font-size="12" font-weight="600" fill="${text}">${title}</text>`);
    parts.push(`<text x="12" y="40" font-family="${FONT_FAMILY}" font-size="10.5" fill="${text}" opacity="0.72">${subtitle}</text>`);
    parts.push(`<text x="12" y="${h - 10}" font-family="${MONO_FONT}" font-size="8.5" font-weight="600" letter-spacing="0.8" fill="${text}" opacity="0.6">${escapeXml(kindLabel)}</text>`);
    parts.push(`<circle cx="${w - 64}" cy="${h - 13}" r="4" fill="${complexityColor}"/>`);
    parts.push(`<text x="${w - 54}" y="${h - 10}" font-family="${FONT_FAMILY}" font-size="10" fill="${text}" opacity="0.85">↓${nodeData.in_degree} ↑${nodeData.out_degree}</text>`);
    parts.push(`</g>`);
  } else if (type === "layerCluster" || type === "subGroupCluster") {
    const data = n.data as unknown as LayerClusterNodeData & { kind?: string; sibling?: boolean; demoted?: boolean };
    const layer = data.layer;
    const isGroup = data.kind === "subGroup";
    const recessed = Boolean(data.sibling || data.demoted);
    const title = escapeXml(truncate(layer.name, Math.floor(w / 8)));
    const subtitle = escapeXml(truncate(layer.description, Math.floor(w / 6)));

    parts.push(`<g transform="translate(${x},${y})">`);
    parts.push(inkBox(recessed ? pal.ink2 : pal.ink));
    parts.push(`<text x="14" y="26" font-family="${MONO_FONT}" font-size="15" font-weight="600" fill="${pal.inkText}">${title}</text>`);
    parts.push(`<text x="14" y="44" font-family="${FONT_FAMILY}" font-size="11" fill="${pal.inkText}" opacity="0.72">${subtitle}</text>`);
    parts.push(`<text x="14" y="${h - 12}" font-family="${MONO_FONT}" font-size="9" font-weight="600" letter-spacing="1" fill="${pal.inkText}" opacity="0.6">${isGroup ? "GROUP" : "LAYER"} · ${layer.file_count} FILES</text>`);
    if (layer.health_score !== null) {
      const healthColor = layer.health_score >= 80 ? pal.success : layer.health_score >= 60 ? pal.warning : pal.error;
      parts.push(`<text x="${w - 14}" y="${h - 12}" font-family="${FONT_FAMILY}" font-size="12" font-weight="600" fill="${healthColor}" text-anchor="end">${Math.round(layer.health_score)}</text>`);
    }
    parts.push(`</g>`);
  } else if (type === "archContainer") {
    const data = n.data as unknown as ArchContainerNodeData;
    const title = escapeXml(truncate(`${data.childCount} files`, Math.floor(w / 8)));

    parts.push(`<g transform="translate(${x},${y})">`);
    parts.push(inkBox("none", true));
    parts.push(`<text x="12" y="22" font-family="${MONO_FONT}" font-size="12" font-weight="600" fill="${pal.textPrimary}">${escapeXml(data.label)}</text>`);
    parts.push(`<text x="12" y="40" font-family="${FONT_FAMILY}" font-size="10.5" fill="${pal.textSecondary}">${title}</text>`);
    parts.push(`</g>`);
  } else if (type === "portal") {
    const data = n.data as unknown as PortalNodeData;

    parts.push(`<g transform="translate(${x},${y})">`);
    parts.push(inkBox("none", true));
    parts.push(`<text x="12" y="22" font-family="${MONO_FONT}" font-size="12" font-weight="600" fill="${pal.textPrimary}">→ ${escapeXml(data.targetLayerName)}</text>`);
    parts.push(`<text x="12" y="40" font-family="${FONT_FAMILY}" font-size="10.5" fill="${pal.textSecondary}">${data.edgeCount} connections</text>`);
    parts.push(`<text x="12" y="${h - 8}" font-family="${MONO_FONT}" font-size="8.5" font-weight="600" letter-spacing="0.8" fill="${pal.textSecondary}" opacity="0.8">PORTAL</text>`);
    parts.push(`</g>`);
  }

  return parts.join("");
}

function nodeCenter(n: Node): { x: number; y: number; w: number; h: number } {
  const w = (n.width as number | undefined) ?? 200;
  const h = (n.height as number | undefined) ?? 100;
  return { x: n.position.x + w / 2, y: n.position.y + h / 2, w, h };
}

function renderEdge(e: Edge, nodesById: Map<string, Node>, pal: InkPalette): string {
  const source = nodesById.get(e.source);
  const target = nodesById.get(e.target);
  if (!source || !target) return "";
  const s = nodeCenter(source);
  const t = nodeCenter(target);
  // attach to closest horizontal edge
  const sy = t.y > s.y ? s.y + s.h / 2 : s.y - s.h / 2;
  const ty = t.y > s.y ? t.y - t.h / 2 : t.y + t.h / 2;
  const sx = s.x;
  const tx = t.x;
  const midY = (sy + ty) / 2;
  const path = `M ${sx} ${sy} C ${sx} ${midY}, ${tx} ${midY}, ${tx} ${ty}`;

  const data = e.data as unknown as C4EdgeData | undefined;
  const label = data?.relation?.label?.trim() ?? "";

  const parts: string[] = [];
  parts.push(
    `<path d="${path}" fill="none" stroke="${pal.edge}" stroke-width="1.25" stroke-dasharray="6 4" marker-end="url(#c4-arrow)"/>`,
  );
  if (label) {
    const lx = (sx + tx) / 2;
    const ly = midY;
    const text = escapeXml(truncate(label, 32));
    const padW = text.length * 6 + 14;
    parts.push(
      `<rect x="${lx - padW / 2}" y="${ly - 9}" width="${padW}" height="17" rx="6" fill="${pal.paper}" stroke="${pal.cluster}" stroke-width="0.75"/>`,
    );
    parts.push(
      `<text x="${lx}" y="${ly + 3}" font-family="${MONO_FONT}" font-size="9.5" fill="${pal.textSecondary}" text-anchor="middle">${text}</text>`,
    );
  }
  return parts.join("");
}

export interface SvgExportOptions {
  /** Optional title baked into the SVG header. */
  title?: string;
}

export function buildC4Svg(
  nodes: Node[],
  edges: Edge[],
  options: SvgExportOptions = {},
): string {
  const bounds = computeBounds(nodes);
  const width = bounds.maxX - bounds.minX + PADDING * 2;
  const height = bounds.maxY - bounds.minY + PADDING * 2 + (options.title ? 28 : 0);
  const titleOffset = options.title ? 28 : 0;
  const tx = -bounds.minX + PADDING;
  const ty = -bounds.minY + PADDING + titleOffset;

  const nodesById = new Map(nodes.map((n) => [n.id, n]));
  const pal = resolveInkPalette();

  const ARCH_NODE_TYPES = new Set(["archFile", "archContainer", "layerCluster", "subGroupCluster", "portal", "scopeFrame"]);

  const edgeMarkup = edges.map((e) => renderEdge(e, nodesById, pal)).join("");
  const nodeMarkup = nodes.map((n) =>
    ARCH_NODE_TYPES.has(n.type ?? "") ? renderArchNode(n, pal) : renderNode(n)
  ).join("");

  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="${Math.round(width)}" height="${Math.round(height)}" viewBox="0 0 ${Math.round(width)} ${Math.round(height)}">
  <defs>
    <marker id="c4-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="${pal.edge}"/>
    </marker>
    <linearGradient id="kg-ember" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="${pal.accent}"/>
      <stop offset="100%" stop-color="${pal.accentEnd}"/>
    </linearGradient>
    <pattern id="kg-grid" width="24" height="24" patternUnits="userSpaceOnUse">
      <path d="M 24 0 L 0 0 0 24" fill="none" stroke="${pal.grid}" stroke-width="1"/>
    </pattern>
  </defs>
  <rect width="100%" height="100%" fill="${pal.canvas}"/>
  <rect width="100%" height="100%" fill="url(#kg-grid)"/>
  ${options.title ? `<text x="${PADDING}" y="22" font-family="${FONT_FAMILY}" font-size="14" font-weight="600" fill="${pal.textPrimary}">${escapeXml(options.title)}</text>` : ""}
  <g transform="translate(${tx},${ty})">
    ${edgeMarkup}
    ${nodeMarkup}
  </g>
</svg>`;
}

export function downloadSvg(svg: string, filename: string): void {
  const blob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
  triggerDownload(blob, filename);
}

export function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
