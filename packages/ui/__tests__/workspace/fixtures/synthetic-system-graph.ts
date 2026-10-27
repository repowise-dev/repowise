/**
 * Seeded synthetic system graphs for the System Map benches and scale tests.
 *
 * Shaped on the eShop reference estate's artifact: every node is a service in
 * its own repo, and edges follow its kind mix (59% package, 20% event, 16%
 * HTTP, 5% gRPC). Package edges fan into a minority of library repos with a
 * Zipf skew, the way eShop's build edges pile onto a few shared libraries;
 * runtime edges join non-library services, mostly inside one group. Edge ids,
 * strategies, confidences and weights follow the core builder's output, so a
 * function that reads a real artifact reads this one the same way.
 */

import type {
  ExtractionDiagnostics,
  LinkStrategy,
  SystemEdge,
  SystemEdgeKind,
  SystemGraph,
  SystemNode,
} from "@repowise-dev/types";

/** A node carrying the optional system group a grouped artifact adds. */
export type SyntheticSystemNode = SystemNode & { group?: string };
export type SyntheticSystemGraph = Omit<SystemGraph, "nodes"> & { nodes: SyntheticSystemNode[] };

export interface SyntheticSystemGraphOptions {
  services: number;
  edges: number;
  /** Groups the services fall into; 1 or fewer leaves `group` off every node. */
  groups?: number;
  seed?: number;
}

/** eShop's measured edge-kind mix: 26, 9, 7 and 2 of its 44 edges. */
export const ESHOP_KIND_MIX: ReadonlyArray<readonly [SystemEdgeKind, number]> = [
  ["package", 26 / 44],
  ["event", 9 / 44],
  ["http", 7 / 44],
  ["grpc", 2 / 44],
];

/** Share of services that are libraries (package-edge targets only). */
const LIBRARY_SHARE = 0.25;
/** Share of runtime edges that stay inside the caller's group. */
const IN_GROUP_SHARE = 0.7;

interface KindShape {
  confidence: readonly number[];
  strategy: readonly LinkStrategy[];
  weight: readonly [number, number];
  contractType: string | null;
  ref: (source: string, target: string, i: number) => string;
}

/** Per-kind values seen in the eShop artifact. */
const KIND_SHAPE: Partial<Record<SystemEdgeKind, KindShape>> = {
  package: {
    confidence: [1],
    strategy: ["manifest"],
    weight: [1, 1],
    contractType: null,
    ref: (source) => `dotnet_project_ref:${source}.csproj`,
  },
  event: {
    confidence: [0.7],
    strategy: ["exact_id"],
    weight: [1, 6],
    contractType: "topic",
    ref: (_s, target, i) => `topic::${target}.event${i}`,
  },
  http: {
    confidence: [0.7, 0.75, 0.85],
    strategy: ["exact_id", "unique_path", "service_mapped", "gateway_route"],
    weight: [2, 6],
    contractType: "http",
    ref: (_s, target, i) => `http::GET::/api/${target}/items${i}`,
  },
  grpc: {
    confidence: [0.65, 0.75],
    strategy: ["exact_id"],
    weight: [1, 2],
    contractType: "grpc",
    ref: (_s, target, i) => `grpc::${target}.Service/Call${i}`,
  },
};

/** mulberry32: small, fast, and identical on every platform for one seed. */
function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Largest-remainder split of `total` by `mix`, so the counts sum exactly. */
function splitByMix(total: number, mix: ReadonlyArray<readonly [SystemEdgeKind, number]>) {
  const raw = mix.map(([kind, share]) => ({ kind, exact: total * share, n: Math.floor(total * share) }));
  let left = total - raw.reduce((s, r) => s + r.n, 0);
  for (const r of [...raw].sort((a, b) => b.exact - b.n - (a.exact - a.n))) {
    if (left-- <= 0) break;
    r.n += 1;
  }
  return raw.map((r) => [r.kind, r.n] as const);
}

function emptyDiagnostics(totalLinks: number): ExtractionDiagnostics {
  return {
    total_providers: 0,
    total_consumers: 0,
    total_links: totalLinks,
    weak_link_count: 0,
    repo_breakdown: [],
    unmatched_consumers: [],
    unmatched_by_reason: {},
    orphan_providers: [],
    providers_by_layer: {},
    consumers_by_layer: {},
    http_consumers_unresolved: 0,
    http_consumer_coverage: null,
  };
}

interface Estate {
  ids: string[];
  groupOf: Map<string, string>;
  libraries: string[];
  runtime: string[];
  runtimeByGroup: Map<string, string[]>;
}

/** Service ids `g03.svc042`: a dotted family per group gives name-family grouping something to find. */
function estate(services: number, groupCount: number): Estate {
  const libraryCount = Math.max(1, Math.round(services * LIBRARY_SHARE));
  const width = String(services - 1).length;
  const groupWidth = String(groupCount - 1).length;
  const ids: string[] = [];
  const groupOf = new Map<string, string>();
  const runtimeByGroup = new Map<string, string[]>();
  for (let i = 0; i < services; i++) {
    const group = `g${String(i % groupCount).padStart(groupWidth, "0")}`;
    // The first `libraryCount` indices are libraries.
    const library = i < libraryCount;
    const id = `${group}.${library ? "lib" : "svc"}${String(i).padStart(width, "0")}`;
    ids.push(id);
    groupOf.set(id, group);
    if (!library) runtimeByGroup.set(group, [...(runtimeByGroup.get(group) ?? []), id]);
  }
  return { ids, groupOf, libraries: ids.slice(0, libraryCount), runtime: ids.slice(libraryCount), runtimeByGroup };
}

type Random = () => number;

function pickFrom<T>(random: Random, items: readonly T[]): T {
  return items[Math.floor(random() * items.length)] as T;
}

/** Endpoint pickers: package edges to Zipf-ranked library hubs, runtime edges mostly in-group. */
function endpointPicker(e: Estate, random: Random): (kind: SystemEdgeKind) => [string, string] {
  const zipf: number[] = [];
  e.libraries.reduce((sum, _l, r) => {
    zipf.push(sum + 1 / (r + 1));
    return sum + 1 / (r + 1);
  }, 0);
  const zipfTotal = zipf[zipf.length - 1] as number;
  const runtimePool = e.runtime.length > 1 ? e.runtime : e.ids;
  return (kind) => {
    if (kind === "package") {
      const source = pickFrom(random, e.ids);
      const x = random() * zipfTotal;
      return [source, e.libraries[zipf.findIndex((c) => c >= x)] as string];
    }
    const source = pickFrom(random, e.runtime.length > 0 ? e.runtime : e.ids);
    const own = e.runtimeByGroup.get(e.groupOf.get(source) as string) ?? [];
    const pool = own.length > 1 && random() < IN_GROUP_SHARE ? own : runtimePool;
    return [source, pickFrom(random, pool)];
  };
}

function placeEdges(e: Estate, edges: number, random: Random): SystemEdge[] {
  const pickEnds = endpointPicker(e, random);
  const out: SystemEdge[] = [];
  const seen = new Set<string>();
  for (const [kind, count] of splitByMix(edges, ESHOP_KIND_MIX)) {
    const shape = KIND_SHAPE[kind] as KindShape;
    let placed = 0;
    // Edges are unique per (source, target, kind), as the core builder merges
    // them; a bounded retry keeps an over-dense request from looping forever.
    for (let attempt = 0; placed < count && attempt < count * 50; attempt++) {
      const [source, target] = pickEnds(kind);
      const id = `${source}->${target}:${kind}`;
      if (source === target || seen.has(id)) continue;
      seen.add(id);
      const [lo, hi] = shape.weight;
      const weight = lo + Math.floor(random() * (hi - lo + 1));
      out.push({
        id,
        source,
        target,
        kind,
        match_type: "exact",
        confidence: pickFrom(random, shape.confidence),
        weight,
        structural: true,
        contract_refs: Array.from({ length: weight }, (_, i) => shape.ref(source, target, i)),
        strategy: pickFrom(random, shape.strategy),
      });
      placed++;
    }
    if (placed < count) {
      throw new Error(`cannot place ${count} distinct ${kind} edges among ${e.ids.length} services`);
    }
  }
  return out;
}

/** Nodes with counts and contract types derived from their edges, as the core builder derives them. */
function nodesFor(e: Estate, edges: readonly SystemEdge[], grouped: boolean): SyntheticSystemNode[] {
  const provided = new Map<string, number>();
  const consumed = new Map<string, number>();
  const types = new Map<string, Set<string>>();
  for (const edge of edges) {
    const type = KIND_SHAPE[edge.kind]?.contractType;
    if (!type) continue;
    provided.set(edge.target, (provided.get(edge.target) ?? 0) + edge.weight);
    consumed.set(edge.source, (consumed.get(edge.source) ?? 0) + edge.weight);
    for (const end of [edge.source, edge.target]) types.set(end, (types.get(end) ?? new Set()).add(type));
  }
  const linked = new Set(edges.flatMap((edge) => [edge.source, edge.target]));
  return e.ids.map((id) => ({
    id,
    repo: id,
    service_path: null,
    name: id,
    kind: "service",
    provider_count: provided.get(id) ?? 0,
    consumer_count: consumed.get(id) ?? 0,
    contract_types: [...(types.get(id) ?? [])].sort(),
    is_orphan_provider: false,
    is_orphan_consumer: false,
    is_isolated: !linked.has(id),
    ...(grouped ? { group: e.groupOf.get(id) as string } : {}),
  }));
}

export function makeSyntheticSystemGraph({
  services,
  edges,
  groups = 1,
  seed = 1,
}: SyntheticSystemGraphOptions): SyntheticSystemGraph {
  if (services < 2) throw new Error("a synthetic system graph needs at least two services");
  const groupCount = Math.max(1, Math.min(groups, services));
  const e = estate(services, groupCount);
  const out = placeEdges(e, edges, rng(seed));
  return {
    version: 2,
    generated_at: "2026-10-24T00:00:00Z",
    nodes: nodesFor(e, out, groupCount > 1),
    edges: out,
    diagnostics: emptyDiagnostics(out.length),
  };
}
