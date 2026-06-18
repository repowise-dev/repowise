"use client";

import { useMemo, useState } from "react";
import { cluster, hierarchy, type HierarchyNode } from "d3-hierarchy";
import { curveBundle, lineRadial } from "d3-shape";
// Value imports must come from the package root, not the `/coupling` subpath:
// the vite/rollup base alias clobbers subpath value resolution. Type-only
// subpath imports are fine (erased before resolution).
import { bandForScore } from "@repowise-dev/types";
import type { CouplingEdge, CouplingNode } from "@repowise-dev/types/coupling";
import type { HealthBand } from "@repowise-dev/types/health";

export interface CouplingGraphProps {
  nodes: CouplingNode[];
  edges: CouplingEdge[];
  /** Pre-cap edge count, for the honest "showing N of M" line. */
  totalEdges?: number;
  /** Controlled focus (sync with the table); falls back to internal hover. */
  focusedPath?: string | null;
  onFocusChange?: (path: string | null) => void;
  /** Square render size in px (viewBox edge). */
  size?: number;
}

/* CSS-var ink per canonical health band (the 3-bucket currency). A file with
 * no health metric resolves to neutral. SVG stroke/fill accept `var()`. */
const NEUTRAL_INK = "var(--color-text-tertiary)";
const BAND_INK: Record<HealthBand, string> = {
  alert: "var(--color-error)",
  warning: "var(--color-caution)",
  healthy: "var(--color-success)",
};

function inkFor(score: number | null): string {
  return score == null ? NEUTRAL_INK : BAND_INK[bandForScore(score)];
}

/** A leaf in the radial cluster: a file, plus its layout angle/radius. */
interface TreeDatum {
  name: string; // path segment (display)
  path?: string; // full original file_path (leaves only)
  node?: CouplingNode;
  children?: TreeDatum[];
}

/**
 * Strip the longest path-segment prefix shared by every file so the radial
 * dendrogram branches on the segments that actually differ (otherwise a deep
 * monorepo prefix like `packages/core/src/...` wastes the whole inner radius on
 * a single unbranching chain). The arc-group labels then read as real modules.
 */
function stripCommonPrefix(paths: string[]): { stripped: string[]; prefix: string[] } {
  if (paths.length === 0) return { stripped: [], prefix: [] };
  const split = paths.map((p) => p.split("/"));
  const first = split[0]!;
  let common = 0;
  for (let i = 0; i < first.length - 1; i++) {
    const seg = first[i];
    if (split.every((s) => s.length > i + 1 && s[i] === seg)) common++;
    else break;
  }
  return {
    stripped: split.map((s) => s.slice(common).join("/")),
    prefix: first.slice(0, common),
  };
}

/** Build a nested folder tree from file paths (leaves carry the node datum). */
function buildTree(nodes: CouplingNode[]): TreeDatum {
  const { stripped } = stripCommonPrefix(nodes.map((n) => n.file_path));
  const root: TreeDatum = { name: "", children: [] };
  nodes.forEach((node, i) => {
    const segs = stripped[i]!.split("/");
    let cursor = root;
    segs.forEach((seg, depth) => {
      const isLeaf = depth === segs.length - 1;
      if (isLeaf) {
        (cursor.children ??= []).push({ name: seg, path: node.file_path, node });
        return;
      }
      let child = cursor.children?.find((c) => c.name === seg && c.path === undefined);
      if (!child) {
        child = { name: seg, children: [] };
        (cursor.children ??= []).push(child);
      }
      cursor = child;
    });
  });
  return root;
}

/** Polar -> cartesian, with 0 rad at 12 o'clock (matches d3 cluster + lineRadial). */
function project(angle: number, radius: number): [number, number] {
  const a = angle - Math.PI / 2;
  return [Math.cos(a) * radius, Math.sin(a) * radius];
}

/**
 * Hierarchical edge-bundling view of change coupling. Files sit on a ring,
 * grouped by their directory hierarchy; an arc between two files means they are
 * frequently committed together. Edges are bundled through the folder tree so
 * the picture reads as module-to-module flow rather than a hairball. Dots are
 * colored by health band; hovering a file lifts its couplings (colored by the
 * partner's health) and dims the rest.
 *
 * Co-change is a temporal hint, not a verified dependency; edge thickness/
 * opacity encode the decay-weighted strength, never a fabricated trend.
 */
export function CouplingGraph({
  nodes,
  edges,
  totalEdges,
  focusedPath,
  onFocusChange,
  size = 760,
}: CouplingGraphProps) {
  const [internalFocus, setInternalFocus] = useState<string | null>(null);
  const focus = focusedPath !== undefined ? focusedPath : internalFocus;
  const setFocus = (p: string | null) => {
    if (onFocusChange) onFocusChange(p);
    else setInternalFocus(p);
  };

  const layout = useMemo(() => {
    if (nodes.length < 2 || edges.length === 0) return null;
    const radius = size / 2 - 96; // leave room for arc labels
    const root = hierarchy<TreeDatum>(buildTree(nodes), (d) => d.children);
    root.sort((a, b) => (a.data.name < b.data.name ? -1 : a.data.name > b.data.name ? 1 : 0));
    cluster<TreeDatum>().size([2 * Math.PI, radius])(root);

    const leaves = root.leaves();
    const leafByPath = new Map<string, HierarchyNode<TreeDatum>>();
    for (const leaf of leaves) if (leaf.data.path) leafByPath.set(leaf.data.path, leaf);

    const line = lineRadial<HierarchyNode<TreeDatum>>()
      .curve(curveBundle.beta(0.82))
      .radius((d) => d.y!)
      .angle((d) => d.x!);

    const maxStrength = Math.max(...edges.map((e) => e.strength), 1);
    const drawn = edges
      .map((e) => {
        const s = leafByPath.get(e.source);
        const t = leafByPath.get(e.target);
        if (!s || !t) return null;
        return { edge: e, d: line(s.path(t)) ?? "" };
      })
      .filter((x): x is { edge: CouplingEdge; d: string } => x !== null);

    // Degree per file -> dot radius emphasis for highly-coupled hubs.
    const degree = new Map<string, number>();
    for (const e of edges) {
      degree.set(e.source, (degree.get(e.source) ?? 0) + 1);
      degree.set(e.target, (degree.get(e.target) ?? 0) + 1);
    }
    const maxNloc = Math.max(...nodes.map((n) => n.nloc), 1);

    // Arc groups: the first stripped segment of each leaf (the module band).
    const groups = new Map<string, { a0: number; a1: number }>();
    for (const leaf of leaves) {
      const top = leaf.ancestors().reverse()[1]; // child of root
      const name = top?.data.name ?? "";
      if (!name) continue;
      const g = groups.get(name);
      if (!g) groups.set(name, { a0: leaf.x!, a1: leaf.x! });
      else {
        g.a0 = Math.min(g.a0, leaf.x!);
        g.a1 = Math.max(g.a1, leaf.x!);
      }
    }

    return { radius, leaves, leafByPath, drawn, degree, maxNloc, maxStrength, groups };
  }, [nodes, edges, size]);

  if (!layout) {
    return (
      <div
        className="flex items-center justify-center rounded-xl border border-dashed border-[var(--color-border-default)] text-center text-sm text-[var(--color-text-tertiary)]"
        style={{ minHeight: size * 0.6 }}
      >
        Not enough shared git history to map coupling yet. Files need to have
        been committed together.
      </div>
    );
  }

  const { radius, leaves, drawn, degree, maxNloc, maxStrength, groups } = layout;
  const c = size / 2;

  // Top-degree hubs are always labelled; the rest reveal on hover. This replaces
  // the all-or-nothing 36-node gate with a curated set that stays legible even
  // on a dense ring.
  const TOP_HUB_LABELS = 14;
  const hubPaths = new Set(
    [...degree.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, TOP_HUB_LABELS)
      .map(([path]) => path),
  );

  // Disambiguate basename collisions: when two files share a basename, prepend
  // the immediate parent dir so the ring labels stay distinct.
  const basenameCounts = new Map<string, number>();
  for (const n of nodes) {
    const base = n.file_path.split("/").pop() ?? n.file_path;
    basenameCounts.set(base, (basenameCounts.get(base) ?? 0) + 1);
  }
  const labelFor = (full: string) => {
    const segs = full.split("/");
    const base = segs.at(-1) ?? full;
    if ((basenameCounts.get(base) ?? 0) > 1 && segs.length > 1) {
      return `${segs.at(-2)}/${base}`;
    }
    return base;
  };

  // Neighbors of the focused file (for dimming + dot emphasis).
  const neighbors = new Set<string>();
  if (focus) {
    for (const e of edges) {
      if (e.source === focus) neighbors.add(e.target);
      if (e.target === focus) neighbors.add(e.source);
    }
  }

  const dotR = (n: CouplingNode) =>
    2 + Math.sqrt(n.nloc / maxNloc) * 2.4 + Math.min((degree.get(n.file_path) ?? 0) / 6, 2.6);

  return (
    <div>
      <svg
        viewBox={`0 0 ${size} ${size}`}
        width="100%"
        role="img"
        aria-label="Change-coupling diagram: files arranged in a ring, arcs link files that change together"
        onMouseLeave={() => setFocus(null)}
      >
        <g transform={`translate(${c},${c})`}>
          {/* Module arc bands + labels around the perimeter. Tiny (≈single
              file) arcs are left unlabelled so their names don't pile up near
              12 o'clock; the remaining module labels are angularly well spread. */}
          {[...groups.entries()].map(([name, g]) => {
            const mid = (g.a0 + g.a1) / 2;
            const [lx, ly] = project(mid, radius + 34);
            const [x0, y0] = project(g.a0 - 0.012, radius + 12);
            const [x1, y1] = project(g.a1 + 0.012, radius + 12);
            const large = g.a1 - g.a0 > Math.PI ? 1 : 0;
            const span = g.a1 - g.a0;
            return (
              <g key={name}>
                <path
                  d={`M ${x0} ${y0} A ${radius + 12} ${radius + 12} 0 ${large} 1 ${x1} ${y1}`}
                  fill="none"
                  stroke="currentColor"
                  className="text-[var(--color-border-strong)]"
                  strokeOpacity={0.5}
                  strokeWidth={1.5}
                  strokeLinecap="round"
                />
                {span >= 0.07 && (
                  <text
                    x={lx}
                    y={ly}
                    fontSize={11}
                    textAnchor="middle"
                    dominantBaseline="middle"
                    className="fill-[var(--color-text-secondary)] font-medium uppercase tracking-wider"
                  >
                    {name}
                  </text>
                )}
              </g>
            );
          })}

          {/* Edges. Quiet hairlines by default; on focus, the focused file's
              couplings resolve to the partner's health color and the rest fade. */}
          <g fill="none">
            {drawn.map(({ edge, d }) => {
              const incident = focus === edge.source || focus === edge.target;
              const dim = focus != null && !incident;
              const partnerPath = focus === edge.source ? edge.target : edge.source;
              const partnerNode = incident
                ? nodes.find((n) => n.file_path === partnerPath)
                : undefined;
              const strengthFrac = edge.strength / maxStrength;
              const stroke = incident ? inkFor(partnerNode?.score ?? null) : NEUTRAL_INK;
              return (
                <path
                  key={`${edge.source}|${edge.target}`}
                  d={d}
                  stroke={stroke}
                  strokeWidth={incident ? 1.1 + strengthFrac * 2.4 : 0.7 + strengthFrac * 0.8}
                  strokeOpacity={incident ? 0.9 : dim ? 0.04 : 0.1 + strengthFrac * 0.14}
                  style={{ transition: "stroke-opacity 160ms ease, stroke-width 160ms ease" }}
                />
              );
            })}
          </g>

          {/* File dots + (focused) labels. */}
          {leaves.map((leaf) => {
            const n = leaf.data.node;
            if (!n) return null;
            const [x, y] = project(leaf.x!, leaf.y!);
            const isFocus = focus === n.file_path;
            const isNeighbor = neighbors.has(n.file_path);
            const dim = focus != null && !isFocus && !isNeighbor;
            const isHub = hubPaths.has(n.file_path);
            // Always label the top hubs; reveal the rest on hover (focus +
            // neighbors). No more all-or-nothing 36-node gate.
            const showLabel = isFocus || isNeighbor || (focus == null && isHub);
            // Labels radiate along each file's own spoke so angularly-adjacent
            // hubs fan out instead of stacking on a shared baseline. Flip the
            // left half so the text never renders upside-down.
            const onLeft = leaf.x! >= Math.PI;
            const labelRot = (leaf.x! * 180) / Math.PI - 90;
            return (
              <g
                key={n.file_path}
                style={{ transition: "opacity 160ms ease", opacity: dim ? 0.18 : 1 }}
                onMouseEnter={() => setFocus(n.file_path)}
                className="cursor-pointer"
              >
                <circle
                  cx={x}
                  cy={y}
                  r={dotR(n) * (isFocus ? 1.5 : 1)}
                  fill={inkFor(n.score)}
                  fillOpacity={0.92}
                  stroke="var(--color-bg-surface)"
                  strokeWidth={isFocus ? 1.6 : 1}
                >
                  <title>{`${n.file_path}${n.score != null ? ` · score ${n.score.toFixed(1)}` : ""} · ${degree.get(n.file_path) ?? 0} couplings`}</title>
                </circle>
                {showLabel ? (
                  <text
                    transform={`rotate(${labelRot}) translate(${leaf.y! + 8},0)${onLeft ? " rotate(180)" : ""}`}
                    dy="0.31em"
                    fontSize={11.5}
                    textAnchor={onLeft ? "end" : "start"}
                    className={
                      isFocus || isHub
                        ? "fill-[var(--color-text-primary)] font-medium"
                        : "fill-[var(--color-text-secondary)]"
                    }
                    style={{ pointerEvents: "none" }}
                  >
                    {labelFor(n.file_path)}
                  </text>
                ) : null}
              </g>
            );
          })}
        </g>
      </svg>

      {/* Legend */}
      <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--color-text-tertiary)]">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full bg-[var(--color-success)]" /> healthy
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full bg-[var(--color-caution)]" /> warning
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full bg-[var(--color-error)]" /> alert
        </span>
        <span className="ml-auto">
          {totalEdges && totalEdges > drawn.length
            ? `showing ${drawn.length} of ${totalEdges} couplings · `
            : `${drawn.length} couplings · `}
          dot size = lines + couplings · line opacity = strength
        </span>
      </div>
    </div>
  );
}
