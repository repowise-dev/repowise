/**
 * The whole-repo Files view: communities as coloured clusters, one weighted
 * band per pair of communities, a few ranked file labels, and each file's own
 * edges revealed on hover or selection (drawn by `overview-layers.ts`).
 *
 * Two steps, both pure over their input:
 *   1. `filterOverviewData` decides what is drawn at all (the data policy).
 *   2. `layoutFilesOverview` adjusts positions and derives the clusters,
 *      bands and label ranking the canvas layers draw from.
 *
 * Measured on this repo's 1,500-node export: 153 nodes are third-party
 * (`external:` / `framework:`) and touch 40% of drawn edges; 5 community pairs
 * carry 81% of the cross-community traffic. That is why third-party is hidden
 * by default and why cross-community structure is drawn as bands rather than
 * as 1,900 individual chords.
 */
import type Graph from "graphology";
import { isExternal } from "@repowise-dev/types";
import type { GraphLink, GraphNode } from "@repowise-dev/types/graph";
import type { SigmaNodeAttributes, SigmaEdgeAttributes } from "./types";
import { isStructuralLink } from "./graphology-adapter";

type SigmaGraph = Graph<SigmaNodeAttributes, SigmaEdgeAttributes>;

// ---- 1. What is drawn ----------------------------------------------------

export interface OverviewData {
  nodes: GraphNode[];
  links: GraphLink[];
  /** Third-party nodes left out (only counted while they are hidden). */
  hiddenExternal: number;
  /** Third-party nodes drawn: all of them when shown, else only a pinned one. */
  shownExternal: number;
  /** Files with no dependency edge to anything drawn. */
  hiddenUnlinked: number;
}

/**
 * Drop third-party nodes unless asked for, then drop files left with no
 * dependency edge. An unlinked file has no position the layout can justify
 * and nothing to reveal on hover, so it is counted rather than drawn.
 * Co-change and low-confidence edges do not count as a link here: they are
 * off by default, so a file held only by them would be a dot with no line.
 *
 * `pinned` (a `?node=` link from a file page, a symbol, a dead-code finding)
 * is always drawn, with every edge it has to a drawn file and the files at
 * the far end of those edges, whatever the rules above say: a link that
 * lands on nothing is worse than a node the overview would have left out.
 */
export function filterOverviewData(
  data: { nodes: GraphNode[]; links: GraphLink[] },
  options: { showExternal: boolean; pinned?: string | null | undefined },
): OverviewData {
  const pinned = options.pinned ?? null;
  const external = new Set<string>();
  if (!options.showExternal) {
    for (const n of data.nodes) {
      if (n.node_id !== pinned && isExternal(n.node_id)) external.add(n.node_id);
    }
  }
  const present = new Set<string>();
  for (const n of data.nodes) if (!external.has(n.node_id)) present.add(n.node_id);

  const linked = new Set<string>();
  if (pinned && present.has(pinned)) linked.add(pinned);
  for (const l of data.links) {
    if (l.source === l.target) continue;
    if (!present.has(l.source) || !present.has(l.target)) continue;
    if (!isStructuralLink(l) && l.source !== pinned && l.target !== pinned) continue;
    linked.add(l.source);
    linked.add(l.target);
  }
  const nodes = data.nodes.filter((n) => linked.has(n.node_id));
  const links = data.links.filter((l) => linked.has(l.source) && linked.has(l.target));
  let shownExternal = 0;
  for (const n of nodes) if (isExternal(n.node_id)) shownExternal++;
  return {
    nodes,
    links,
    hiddenExternal: external.size,
    shownExternal,
    hiddenUnlinked: present.size - nodes.length,
  };
}

// ---- 2. Layout and derived structure ------------------------------------

export interface OverviewCluster {
  communityId: number;
  /** Centroid, graph coordinates. */
  cx: number;
  cy: number;
  /** 85th-percentile member distance from the centroid, graph units. */
  r: number;
  count: number;
}

export interface OverviewBundle {
  a: number;
  b: number;
  /** Structural file edges between the two communities, either direction. */
  count: number;
  /** sqrt(count / heaviest), 0..1: stroke width and opacity. */
  weight: number;
  /** Quadratic curve in graph coordinates: rim of `a`, control, rim of `b`.
   *  A Bezier survives the camera's scale-and-translate unchanged, so the
   *  per-frame work is three point transforms. */
  x0: number;
  y0: number;
  cx: number;
  cy: number;
  x1: number;
  y1: number;
}

export interface OverviewMeta {
  clusters: OverviewCluster[];
  bundles: OverviewBundle[];
  /** Pairs the bands leave out (below the traffic floor), for the key. */
  omittedPairs: number;
}

const META_KEY = "filesOverview";

/** Read the overview meta a graph was laid out with, if any. */
export function getOverviewMeta(graph: SigmaGraph | null | undefined): OverviewMeta | null {
  if (!graph) return null;
  return (graph.getAttribute(META_KEY as never) as OverviewMeta | undefined) ?? null;
}

/** A community needs this many drawn files to be a cluster, with a name and
 *  bands; a smaller one is seated beside the cluster it links to. */
const CLUSTER_MIN = 8;
/** Where a small community is seated, in multiples of its host's radius. */
const STRAY_RIM = 1.15;
/** Clusters are pushed apart by this factor about the origin, and each is
 *  opened up about its own centroid by `CLUSTER_OPEN`. Post-transform only:
 *  the golden-angle seed that places them (`buildFileGraph`) is unchanged,
 *  this rescales what it produced. Spreading opens room for bands and names
 *  between clusters; opening lowers the overlap inside them (the seed packs a
 *  community's files into a disc sized for density, not for reading), and
 *  keeps a cluster from reading as a dot in the space spreading made. */
const CLUSTER_SPREAD = 1.3;
const CLUSTER_OPEN = 1.35;
/** Band floor: a pair needs at least this share of cross-community traffic. */
const BUNDLE_MIN_SHARE = 0.015;
const BUNDLE_MIN_COUNT = 3;
const BUNDLE_MAX = 16;
/** Labels at overview zoom: the global top N by degree, plus the top file of
 *  every community big enough to be read as a place. Collisions between them
 *  are resolved per frame by the label drawer (`use-sigma`). */
const GLOBAL_LABELS = 6;
const COMMUNITY_LABEL_MIN = 15;

/** A filename that says nothing alone gets its folder. */
const GENERIC_FILE =
  /^(index\.[a-z]+|__init__\.py|__main__\.py|mod\.rs|lib\.rs|main\.[a-z]+|models?\.py|types?\.ts|utils?\.[a-z]+|constants\.[a-z]+|page\.tsx|layout\.tsx|route\.ts|app\.[a-z]+|base\.[a-z]+|common\.[a-z]+|helpers?\.[a-z]+|schemas?\.py|config\.[a-z]+|cli\.py|api\.[a-z]+)$/i;

export function overviewFileLabel(path: string): string {
  const parts = path.split("/");
  const last = parts[parts.length - 1] ?? path;
  return GENERIC_FILE.test(last) && parts.length >= 2 ? parts.slice(-2).join("/") : last;
}

function hash(s: string): number {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

/**
 * Lay a freshly built whole-repo file graph out for the overview and record
 * what the canvas layers need. Mutates positions and labels in place, once:
 * a second call returns the stored meta (React may run a memo twice).
 */
export function layoutFilesOverview(graph: SigmaGraph): OverviewMeta {
  const existing = getOverviewMeta(graph);
  if (existing) return existing;

  // Degree over the dependency edges the overview reveals.
  const degree = new Map<string, number>();
  graph.forEachEdge((_e, attrs, s, t) => {
    if (attrs.edgeKind !== "internal" && attrs.edgeKind !== "crossCommunity") return;
    degree.set(s, (degree.get(s) ?? 0) + 1);
    degree.set(t, (degree.get(t) ?? 0) + 1);
  });

  const members = new Map<number, string[]>();
  graph.forEachNode((node, a) => {
    const list = members.get(a.communityId);
    if (list) list.push(node);
    else members.set(a.communityId, [node]);
  });
  const centroid = (ids: string[]) => {
    let x = 0;
    let y = 0;
    for (const id of ids) {
      x += graph.getNodeAttribute(id, "x");
      y += graph.getNodeAttribute(id, "y");
    }
    return { x: x / ids.length, y: y / ids.length };
  };
  const bigIds = [...members.keys()].filter((id) => members.get(id)!.length >= CLUSTER_MIN);
  const big = new Set(bigIds);

  // Spacing first, so everything below measures the final positions.
  for (const id of bigIds) {
    const ids = members.get(id)!;
    const c = centroid(ids);
    for (const node of ids) {
      const a = graph.getNodeAttributes(node);
      graph.mergeNodeAttributes(node, {
        x: c.x * CLUSTER_SPREAD + (a.x - c.x) * CLUSTER_OPEN,
        y: c.y * CLUSTER_SPREAD + (a.y - c.y) * CLUSTER_OPEN,
      });
    }
  }

  const clusters: OverviewCluster[] = [];
  const clusterOf = new Map<number, OverviewCluster>();
  for (const id of bigIds) {
    const ids = members.get(id)!;
    const c = centroid(ids);
    const d = ids
      .map((n) => {
        const a = graph.getNodeAttributes(n);
        return Math.hypot(a.x - c.x, a.y - c.y);
      })
      .sort((p, q) => p - q);
    const cluster: OverviewCluster = {
      communityId: id,
      cx: c.x,
      cy: c.y,
      r: Math.max(d[Math.floor(d.length * 0.85)] ?? 0, 12),
      count: ids.length,
    };
    clusters.push(cluster);
    clusterOf.set(id, cluster);
  }
  clusters.sort((p, q) => q.count - p.count || p.communityId - q.communityId);

  // Small communities sit on the far golden-angle ring, nowhere near what
  // they touch, and a speck out there also sets the extent the camera frames.
  // Seat each, as a group, just outside the rim of the cluster its files link
  // to most. Placement only: membership and colour stay their own. Files whose
  // group links to no cluster follow the first neighbour that found a seat.
  const seated = new Set<string>();
  const unseated: string[] = [];
  for (const [cid, ids] of members) {
    if (big.has(cid)) continue;
    const pull = new Map<number, number>();
    for (const node of ids) {
      graph.forEachNeighbor(node, (_n, na) => {
        if (big.has(na.communityId)) pull.set(na.communityId, (pull.get(na.communityId) ?? 0) + 1);
      });
    }
    let best: number | null = null;
    let bestN = 0;
    for (const [c, n] of pull) {
      if (n > bestN || (n === bestN && best !== null && c < best)) {
        best = c;
        bestN = n;
      }
    }
    if (best === null) {
      unseated.push(...ids);
      continue;
    }
    const host = clusterOf.get(best)!;
    const g = centroid(ids);
    let spread = 0;
    for (const node of ids) {
      const a = graph.getNodeAttributes(node);
      spread = Math.max(spread, Math.hypot(a.x - g.x, a.y - g.y));
    }
    const radius = host.r * STRAY_RIM + spread;
    const angle = ((hash(`community:${cid}`) % 1000) / 1000) * Math.PI * 2;
    const dx = host.cx + radius * Math.cos(angle) - g.x;
    const dy = host.cy + radius * Math.sin(angle) - g.y;
    for (const node of ids) {
      const a = graph.getNodeAttributes(node);
      graph.mergeNodeAttributes(node, { x: a.x + dx, y: a.y + dy });
      seated.add(node);
    }
  }
  for (const node of unseated) {
    const anchor = graph.findNeighbor(node, (n) => seated.has(n));
    if (!anchor) continue;
    const a = graph.getNodeAttributes(anchor);
    const angle = ((hash(node) % 1000) / 1000) * Math.PI * 2;
    const step = 8 + a.size * 2;
    graph.mergeNodeAttributes(node, { x: a.x + step * Math.cos(angle), y: a.y + step * Math.sin(angle) });
    seated.add(node);
  }

  // Bands: one per unordered community pair, counted over structural edges.
  const pairs = new Map<string, { a: number; b: number; count: number }>();
  graph.forEachEdge((_e, attrs, _s, _t, sa, ta) => {
    if (attrs.edgeKind !== "crossCommunity") return;
    const ca = sa.communityId;
    const cb = ta.communityId;
    if (ca === cb || !big.has(ca) || !big.has(cb)) return;
    const [lo, hi] = ca < cb ? [ca, cb] : [cb, ca];
    const key = `${lo}:${hi}`;
    const p = pairs.get(key);
    if (p) p.count++;
    else pairs.set(key, { a: lo, b: hi, count: 1 });
  });
  const ranked = [...pairs.values()].sort((p, q) => q.count - p.count || p.a - q.a || p.b - q.b);
  const total = ranked.reduce((s, p) => s + p.count, 0);
  const heaviest = ranked[0]?.count ?? 1;
  const kept = ranked
    .filter((p) => p.count >= Math.max(BUNDLE_MIN_COUNT, total * BUNDLE_MIN_SHARE))
    .slice(0, BUNDLE_MAX);
  const bundles = kept.map((p) =>
    routeBundle(clusterOf.get(p.a)!, clusterOf.get(p.b)!, clusters, p.count, heaviest),
  );

  // Labels: every file gets its short name; only the ranked ones are forced
  // at overview zoom, the rest appear as the reader zooms in.
  const forced = new Set<string>();
  const byDegree = [...degree.entries()].sort((p, q) => q[1] - p[1] || (p[0] < q[0] ? -1 : 1));
  for (const [n] of byDegree.slice(0, GLOBAL_LABELS)) forced.add(n);
  for (const cluster of clusters) {
    if (cluster.count < COMMUNITY_LABEL_MIN) continue;
    let top: string | null = null;
    for (const n of members.get(cluster.communityId)!) {
      if (top === null || (degree.get(n) ?? 0) > (degree.get(top) ?? 0)) top = n;
    }
    if (top) forced.add(top);
  }
  graph.updateEachNodeAttributes(
    (node, a) => ({ ...a, label: overviewFileLabel(a.fullPath || node), forceLabel: forced.has(node) }),
    { attributes: ["label", "forceLabel"] },
  );

  // No arrowheads at rest: direction is shown on the focused file's own
  // edges, where it can be read. A curve program without the arrow is also
  // the cheaper of the two.
  graph.updateEachEdgeAttributes(
    (_e, a) => (a.type === "curvedArrow" ? { ...a, type: "curved" } : a.type === "arrow" ? { ...a, type: "line" } : a),
    { attributes: ["type"] },
  );

  const meta: OverviewMeta = { clusters, bundles, omittedPairs: ranked.length - kept.length };
  graph.setAttribute(META_KEY as never, meta as never);
  return meta;
}

/** Candidate bends, as a fraction of the chord, in order of preference. */
const BENDS = [0.16, -0.16, 0.3, -0.3, 0.06, -0.06, 0.45, -0.45];

/**
 * Route one band. A straight chord between two clusters often crosses a third,
 * which reads as a dependency on it. Try a few bends and keep the one whose
 * curve passes through the fewest other clusters; ties keep the gentler bend.
 * Eight candidates by twelve samples by the cluster count, once per layout.
 */
function routeBundle(
  from: OverviewCluster,
  to: OverviewCluster,
  clusters: OverviewCluster[],
  count: number,
  heaviest: number,
): OverviewBundle {
  const dx = to.cx - from.cx;
  const dy = to.cy - from.cy;
  const len = Math.hypot(dx, dy) || 1;
  const nx = -dy / len;
  const ny = dx / len;
  const mx = (from.cx + to.cx) / 2;
  const my = (from.cy + to.cy) / 2;

  let best = { cx: mx + nx * len * BENDS[0]!, cy: my + ny * len * BENDS[0]!, hits: Infinity };
  for (const bend of BENDS) {
    const cx = mx + nx * len * bend;
    const cy = my + ny * len * bend;
    let hits = 0;
    for (let i = 2; i <= 10; i++) {
      const t = i / 12;
      const u = 1 - t;
      const px = u * u * from.cx + 2 * u * t * cx + t * t * to.cx;
      const py = u * u * from.cy + 2 * u * t * cy + t * t * to.cy;
      for (const c of clusters) {
        if (c === from || c === to) continue;
        if (Math.hypot(px - c.cx, py - c.cy) < c.r) hits++;
      }
    }
    if (hits < best.hits) best = { cx, cy, hits };
    if (hits === 0) break;
  }

  // Leave each cluster at its rim, heading for the control point, so the band
  // grows out of the cluster's edge instead of meeting its neighbours in the
  // middle of it.
  const rim = (c: OverviewCluster) => {
    const ux = best.cx - c.cx;
    const uy = best.cy - c.cy;
    const ul = Math.hypot(ux, uy) || 1;
    return { x: c.cx + (ux / ul) * c.r, y: c.cy + (uy / ul) * c.r };
  };
  const p0 = rim(from);
  const p1 = rim(to);
  return {
    a: from.communityId,
    b: to.communityId,
    count,
    weight: Math.sqrt(count / heaviest),
    x0: p0.x,
    y0: p0.y,
    cx: best.cx,
    cy: best.cy,
    x1: p1.x,
    y1: p1.y,
  };
}
