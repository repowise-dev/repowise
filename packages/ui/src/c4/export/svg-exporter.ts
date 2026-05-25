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
import { THEME } from "../theme/theme-variables";

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

function renderArchNode(n: Node): string {
  const type = n.type ?? "";
  const w = (n.width as number | undefined) ?? 200;
  const h = (n.height as number | undefined) ?? 100;
  const x = n.position.x;
  const y = n.position.y;
  const parts: string[] = [];

  if (type === "archFile") {
    const data = n.data as unknown as ArchFileNodeData;
    const nodeData = data.node;
    const tone = nodeData.node_type as ToneName;
    const style = TONE_STYLES[tone] ?? TONE_STYLES.file;
    const kindLabel = nodeData.language
      ? `${nodeData.node_type.toUpperCase()} · ${nodeData.language}`
      : nodeData.node_type.toUpperCase();
    const title = escapeXml(truncate(nodeData.name, Math.floor(w / 8)));
    const subtitle = escapeXml(truncate(nodeData.summary, Math.floor(w / 6)));
    const complexityColor = THEME.complexity[nodeData.complexity] ?? "#94a3b8";

    parts.push(`<g transform="translate(${x},${y})">`);
    parts.push(`<rect width="${w}" height="${h}" rx="8" ry="8" fill="${style.bg}" stroke="${style.border}" stroke-width="1.5"/>`);
    parts.push(`<rect width="${w}" height="${BAND_HEIGHT}" rx="8" ry="8" fill="${style.band}"/>`);
    parts.push(`<rect y="${BAND_HEIGHT - 8}" width="${w}" height="8" fill="${style.band}"/>`);
    parts.push(`<text x="8" y="${BAND_HEIGHT - 5}" font-family="${FONT_FAMILY}" font-size="9" font-weight="600" letter-spacing="0.6" fill="${style.text}" opacity="0.85">${escapeXml(kindLabel)}</text>`);
    parts.push(`<text x="10" y="${BAND_HEIGHT + 20}" font-family="${FONT_FAMILY}" font-size="13" font-weight="600" fill="${style.text}">${title}</text>`);
    parts.push(`<text x="10" y="${BAND_HEIGHT + 36}" font-family="${FONT_FAMILY}" font-size="11" fill="${style.text}" opacity="0.75">${subtitle}</text>`);
    parts.push(`<circle cx="14" cy="${h - 12}" r="4" fill="${complexityColor}"/>`);
    parts.push(`<text x="24" y="${h - 8}" font-family="${FONT_FAMILY}" font-size="10" fill="${style.text}" opacity="0.8">↓${nodeData.in_degree} ↑${nodeData.out_degree}</text>`);
    parts.push(`</g>`);
  } else if (type === "layerCluster") {
    const data = n.data as unknown as LayerClusterNodeData;
    const layer = data.layer;
    const style = TONE_STYLES.layerCluster;
    const title = escapeXml(truncate(layer.name, Math.floor(w / 8)));
    const subtitle = escapeXml(truncate(layer.description, Math.floor(w / 6)));

    parts.push(`<g transform="translate(${x},${y})">`);
    parts.push(`<rect width="${w}" height="${h}" rx="8" ry="8" fill="${style.bg}" stroke="${style.border}" stroke-width="1.5"/>`);
    parts.push(`<rect width="${w}" height="${BAND_HEIGHT}" rx="8" ry="8" fill="${style.band}"/>`);
    parts.push(`<rect y="${BAND_HEIGHT - 8}" width="${w}" height="8" fill="${style.band}"/>`);
    parts.push(`<text x="8" y="${BAND_HEIGHT - 5}" font-family="${FONT_FAMILY}" font-size="9" font-weight="600" letter-spacing="0.6" fill="${style.text}" opacity="0.85">LAYER</text>`);
    parts.push(`<text x="10" y="${BAND_HEIGHT + 20}" font-family="${FONT_FAMILY}" font-size="13" font-weight="600" fill="${style.text}">${title}</text>`);
    parts.push(`<text x="10" y="${BAND_HEIGHT + 36}" font-family="${FONT_FAMILY}" font-size="11" fill="${style.text}" opacity="0.75">${subtitle}</text>`);
    parts.push(`<text x="10" y="${h - 8}" font-family="${FONT_FAMILY}" font-size="10" fill="${style.text}" opacity="0.8">${layer.file_count} files</text>`);
    if (layer.health_score !== null) {
      const healthColor = layer.health_score >= 80 ? "#4ade80" : layer.health_score >= 60 ? "#fbbf24" : "#f87171";
      parts.push(`<text x="${w - 10}" y="${h - 8}" font-family="${FONT_FAMILY}" font-size="12" font-weight="600" fill="${healthColor}" text-anchor="end">${Math.round(layer.health_score)}</text>`);
    }
    parts.push(`</g>`);
  } else if (type === "archContainer") {
    const data = n.data as unknown as ArchContainerNodeData;
    const style = TONE_STYLES.container;
    const title = escapeXml(truncate(`${data.childCount} files`, Math.floor(w / 8)));

    parts.push(`<g transform="translate(${x},${y})">`);
    parts.push(`<rect width="${w}" height="${h}" rx="8" ry="8" fill="${style.bg}" stroke="${style.border}" stroke-width="1.5" stroke-dasharray="6 3"/>`);
    parts.push(`<rect width="${w}" height="${BAND_HEIGHT}" rx="8" ry="8" fill="${style.band}"/>`);
    parts.push(`<rect y="${BAND_HEIGHT - 8}" width="${w}" height="8" fill="${style.band}"/>`);
    parts.push(`<text x="8" y="${BAND_HEIGHT - 5}" font-family="${FONT_FAMILY}" font-size="9" font-weight="600" letter-spacing="0.6" fill="${style.text}" opacity="0.85">${escapeXml(data.label)}</text>`);
    parts.push(`<text x="10" y="${BAND_HEIGHT + 20}" font-family="${FONT_FAMILY}" font-size="13" font-weight="600" fill="${style.text}">${title}</text>`);
    parts.push(`</g>`);
  } else if (type === "portal") {
    const data = n.data as unknown as PortalNodeData;
    const style = TONE_STYLES.portal;

    parts.push(`<g transform="translate(${x},${y})">`);
    parts.push(`<rect width="${w}" height="${h}" rx="8" ry="8" fill="${style.bg}" stroke="${style.border}" stroke-width="1.5" stroke-dasharray="6 3"/>`);
    parts.push(`<rect width="${w}" height="${BAND_HEIGHT}" rx="8" ry="8" fill="${style.band}"/>`);
    parts.push(`<rect y="${BAND_HEIGHT - 8}" width="${w}" height="8" fill="${style.band}"/>`);
    parts.push(`<text x="8" y="${BAND_HEIGHT - 5}" font-family="${FONT_FAMILY}" font-size="9" font-weight="600" letter-spacing="0.6" fill="${style.text}" opacity="0.85">PORTAL</text>`);
    parts.push(`<text x="10" y="${BAND_HEIGHT + 20}" font-family="${FONT_FAMILY}" font-size="13" font-weight="600" fill="${style.text}">→ ${escapeXml(data.targetLayerName)}</text>`);
    parts.push(`<text x="10" y="${BAND_HEIGHT + 36}" font-family="${FONT_FAMILY}" font-size="11" fill="${style.text}" opacity="0.75">${data.edgeCount} connections</text>`);
    parts.push(`</g>`);
  }

  return parts.join("");
}

function nodeCenter(n: Node): { x: number; y: number; w: number; h: number } {
  const w = (n.width as number | undefined) ?? 200;
  const h = (n.height as number | undefined) ?? 100;
  return { x: n.position.x + w / 2, y: n.position.y + h / 2, w, h };
}

function renderEdge(e: Edge, nodesById: Map<string, Node>): string {
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
    `<path d="${path}" fill="none" stroke="#475569" stroke-width="1.2" marker-end="url(#c4-arrow)"/>`,
  );
  if (label) {
    const lx = (sx + tx) / 2;
    const ly = midY;
    const text = escapeXml(truncate(label, 32));
    const padW = text.length * 6 + 10;
    parts.push(
      `<rect x="${lx - padW / 2}" y="${ly - 9}" width="${padW}" height="16" rx="3" fill="rgba(15,23,42,0.85)" stroke="#334155" stroke-width="0.5"/>`,
    );
    parts.push(
      `<text x="${lx}" y="${ly + 3}" font-family="${FONT_FAMILY}" font-size="10" fill="#cbd5e1" text-anchor="middle">${text}</text>`,
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

  const ARCH_NODE_TYPES = new Set(["archFile", "archContainer", "layerCluster", "portal"]);

  const edgeMarkup = edges.map((e) => renderEdge(e, nodesById)).join("");
  const nodeMarkup = nodes.map((n) =>
    ARCH_NODE_TYPES.has(n.type ?? "") ? renderArchNode(n) : renderNode(n)
  ).join("");

  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="${Math.round(width)}" height="${Math.round(height)}" viewBox="0 0 ${Math.round(width)} ${Math.round(height)}">
  <defs>
    <marker id="c4-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#475569"/>
    </marker>
  </defs>
  <rect width="100%" height="100%" fill="#0b1220"/>
  ${options.title ? `<text x="${PADDING}" y="22" font-family="${FONT_FAMILY}" font-size="14" font-weight="600" fill="#e5e7eb">${escapeXml(options.title)}</text>` : ""}
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
