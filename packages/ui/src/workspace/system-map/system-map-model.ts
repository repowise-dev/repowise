/**
 * Pure joins the System Map's drawer, findings and prompts read from. The
 * system graph names services; contracts, links and diagnostics name files.
 * Everything here maps one onto the other, so the drawer, the rows below the
 * map and the AI prompts count the same things the same way.
 */

import type {
  DependencyCycle,
  ExtractionDiagnostics,
  SystemEdge,
  SystemEdgeKind,
  SystemGraph,
  SystemNode,
  WorkspaceContractLinkEntry,
} from "@repowise-dev/types/workspace";

/** The contract fields the map reads. Structural, so the host's wire type fits. */
export interface SystemMapContract {
  contract_id: string;
  contract_type: string;
  role: string;
  repo: string;
  file_path: string;
  line?: number | null;
  symbol_name?: string;
}

/** Everything the host fetched about one repository's contracts. */
export interface SystemMapRepoContracts {
  contracts: SystemMapContract[];
  links: WorkspaceContractLinkEntry[];
  /** Contracts the repo declares; above `contracts.length` when the fetch was capped. */
  total: number;
}

// ---------------------------------------------------------------------------
// Health: the canonical three bands beside a health mark.
// ---------------------------------------------------------------------------

export interface HealthMark {
  /** 0-10, one decimal. */
  value: string;
  label: "Healthy" | "Warning" | "Alert";
  color: string;
}

/** Repo health arrives 0-100; the product reads it 0-10 in three bands. */
export function healthMark(score100: number): HealthMark {
  const score = score100 / 10;
  const value = score.toFixed(1);
  if (score >= 8) return { value, label: "Healthy", color: "var(--color-success)" };
  if (score >= 4) return { value, label: "Warning", color: "var(--color-warning)" };
  return { value, label: "Alert", color: "var(--color-error)" };
}

// ---------------------------------------------------------------------------
// Files to services.
// ---------------------------------------------------------------------------

/**
 * Resolves `(repo, file)` to the node that owns it on the drawn graph. A file
 * belongs to the deepest declared service path containing it, else to the
 * repo-root node; in repo view every file belongs to its repo.
 */
export type ServiceResolver = (repo: string, file: string) => string;

export function serviceResolver(graph: SystemGraph, collapsed: boolean): ServiceResolver {
  if (collapsed) return (repo) => repo;
  const paths = new Map<string, string[]>();
  for (const n of graph.nodes) {
    if (!n.service_path) continue;
    const list = paths.get(n.repo) ?? [];
    list.push(n.service_path);
    paths.set(n.repo, list);
  }
  // Longest first, so a nested service wins over its parent.
  for (const list of paths.values()) list.sort((a, b) => b.length - a.length);
  return (repo, file) => {
    for (const p of paths.get(repo) ?? []) {
      if (file === p || file.startsWith(`${p}/`)) return `${repo}::${p}`;
    }
    return repo;
  };
}

// ---------------------------------------------------------------------------
// A service's contracts.
// ---------------------------------------------------------------------------

export interface ServiceContractRow extends SystemMapContract {
  /** Matched links this contract takes part in, on its own side. */
  link_count: number;
}

export interface ServiceContractSummary {
  provides: Record<string, number>;
  consumes: Record<string, number>;
  providedTotal: number;
  consumedTotal: number;
  /** Linked contracts first, then providers, then by id. */
  rows: ServiceContractRow[];
}

function linkKey(repo: string, file: string, id: string): string {
  return `${repo}\u0000${file}\u0000${id}`;
}

export function summarizeServiceContracts(
  data: SystemMapRepoContracts,
  nodeId: string,
  resolve: ServiceResolver,
): ServiceContractSummary {
  const linkCount = new Map<string, number>();
  for (const lk of data.links) {
    const p = linkKey(lk.provider_repo, lk.provider_file, lk.contract_id);
    const c = linkKey(lk.consumer_repo, lk.consumer_file, lk.contract_id);
    linkCount.set(p, (linkCount.get(p) ?? 0) + 1);
    linkCount.set(c, (linkCount.get(c) ?? 0) + 1);
  }

  const provides: Record<string, number> = {};
  const consumes: Record<string, number> = {};
  const rows: ServiceContractRow[] = [];
  const seen = new Set<string>();
  for (const c of data.contracts) {
    if (resolve(c.repo, c.file_path) !== nodeId) continue;
    const bucket = c.role === "consumer" ? consumes : provides;
    bucket[c.contract_type] = (bucket[c.contract_type] ?? 0) + 1;
    // One file can declare the same id twice; the Contracts page keys on the
    // triple, so the list does too.
    const key = linkKey(c.repo, c.file_path, c.contract_id);
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({ ...c, link_count: linkCount.get(key) ?? 0 });
  }
  rows.sort(
    (a, b) =>
      b.link_count - a.link_count ||
      Number(a.role === "consumer") - Number(b.role === "consumer") ||
      a.contract_id.localeCompare(b.contract_id),
  );

  const sum = (r: Record<string, number>) => Object.values(r).reduce((a, b) => a + b, 0);
  return { provides, consumes, providedTotal: sum(provides), consumedTotal: sum(consumes), rows };
}

// ---------------------------------------------------------------------------
// A service's diagnostics.
// ---------------------------------------------------------------------------

export interface ServiceDiagnostics {
  unmatched: number;
  unmatchedByReason: Record<string, number>;
  unusedProviders: number;
}

export function serviceDiagnostics(
  diagnostics: ExtractionDiagnostics | null | undefined,
  nodeId: string,
  resolve: ServiceResolver,
): ServiceDiagnostics {
  const out: ServiceDiagnostics = { unmatched: 0, unmatchedByReason: {}, unusedProviders: 0 };
  if (!diagnostics) return out;
  for (const u of diagnostics.unmatched_consumers ?? []) {
    if (resolve(u.repo, u.file_path) !== nodeId) continue;
    out.unmatched += 1;
    out.unmatchedByReason[u.reason] = (out.unmatchedByReason[u.reason] ?? 0) + 1;
  }
  for (const o of diagnostics.orphan_providers ?? []) {
    if (resolve(o.repo, o.file_path) === nodeId) out.unusedProviders += 1;
  }
  return out;
}

// ---------------------------------------------------------------------------
// An edge's evidence.
// ---------------------------------------------------------------------------

/** The contract type each structural edge kind is built from. */
const KIND_CONTRACT_TYPE: Partial<Record<SystemEdgeKind, string>> = {
  http: "http",
  grpc: "grpc",
  socket: "socket",
  event: "topic",
  package: "code",
  db: "data",
};

/**
 * The matched links an edge aggregates. Edge direction is consumer to
 * provider, so the link's consumer must resolve to the source and its provider
 * to the target. The edge's own refs are capped; the links are not.
 */
export function edgeLinks(
  edge: SystemEdge,
  links: readonly WorkspaceContractLinkEntry[],
  resolve: ServiceResolver,
): WorkspaceContractLinkEntry[] {
  if (!edge.structural) return [];
  const type = KIND_CONTRACT_TYPE[edge.kind];
  const refs = new Set(edge.contract_refs);
  const seen = new Set<string>();
  const out: WorkspaceContractLinkEntry[] = [];
  for (const lk of links) {
    if (type ? lk.contract_type !== type : !refs.has(lk.contract_id)) continue;
    if (resolve(lk.consumer_repo, lk.consumer_file) !== edge.source) continue;
    if (resolve(lk.provider_repo, lk.provider_file) !== edge.target) continue;
    const key = `${linkKey(lk.provider_repo, lk.provider_file, lk.contract_id)}\u0000${lk.consumer_file}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(lk);
  }
  out.sort((a, b) => a.contract_id.localeCompare(b.contract_id));
  return out;
}

/** Co-change refs are `source_file~target_file`. */
export function coChangePairs(edge: SystemEdge): [string, string | null][] {
  return edge.contract_refs.map((ref) => {
    const split = ref.indexOf("~");
    return split === -1 ? [ref, null] : [ref.slice(0, split), ref.slice(split + 1)];
  });
}

/** Structural refs repeat when two files use one contract; the list shows it once. */
export function uniqueRefs(edge: SystemEdge): string[] {
  return [...new Set(edge.contract_refs)];
}

// ---------------------------------------------------------------------------
// Words.
// ---------------------------------------------------------------------------

/** What an edge's weight counts, singular and plural. */
const WEIGHT_UNIT: Record<SystemEdgeKind, [string, string]> = {
  http: ["endpoint called", "endpoints called"],
  grpc: ["method called", "methods called"],
  socket: ["socket channel", "socket channels"],
  event: ["topic", "topics"],
  package: ["symbol imported", "symbols imported"],
  db: ["table use", "table uses"],
  co_change: ["file pair changed together", "file pairs changed together"],
};

export function weightLabel(edge: Pick<SystemEdge, "kind" | "weight">): string {
  const unit = WEIGHT_UNIT[edge.kind] ?? ["contract", "contracts"];
  return `${edge.weight.toLocaleString()} ${edge.weight === 1 ? unit[0] : unit[1]}`;
}

/** The canvas label's unit: short, because it sits on a line between two boxes. */
const SHORT_UNIT: Record<SystemEdgeKind, string> = {
  http: "endpoints",
  grpc: "methods",
  socket: "channels",
  event: "topics",
  package: "symbols",
  db: "table uses",
  co_change: "file pairs",
};

export function weightShort(edge: Pick<SystemEdge, "kind" | "weight">): string {
  return `${edge.weight.toLocaleString()} ${SHORT_UNIT[edge.kind] ?? "contracts"}`;
}

/** The relationship as a sentence a developer would say. */
export function edgeSentence(
  edge: Pick<SystemEdge, "kind">,
  source: string,
  target: string,
): string {
  switch (edge.kind) {
    case "http":
      return `${source} calls ${target} over HTTP.`;
    case "grpc":
      return `${source} calls ${target} over gRPC.`;
    case "socket":
      return `${source} connects to ${target} over a socket.`;
    case "event":
      return `${source} consumes events ${target} publishes.`;
    case "package":
      return `${source} imports code from ${target}.`;
    case "db":
      return `${source} uses database tables ${target} defines.`;
    case "co_change":
      return `Files in ${source} and ${target} change in the same commits.`;
    default:
      return `${source} depends on ${target}.`;
  }
}

export const STRUCTURAL_MEANING =
  "Structural: a contract or import in code. Changing the provider side can break the consumer.";
export const BEHAVIORAL_MEANING =
  "Behavioral: a pattern in git history, not a call. These services tend to change together, which does not prove one depends on the other.";

// ---------------------------------------------------------------------------
// Cycles and neighbours.
// ---------------------------------------------------------------------------

export function cyclesThrough(cycles: readonly DependencyCycle[], nodeId: string): DependencyCycle[] {
  return cycles.filter((c) => c.nodes.includes(nodeId));
}

export function cyclesWithEdge(cycles: readonly DependencyCycle[], edgeId: string): DependencyCycle[] {
  return cycles.filter((c) => c.edge_ids.includes(edgeId));
}

export interface NeighbourGroup {
  kind: SystemEdgeKind;
  edges: SystemEdge[];
}

/** A node's edges in one direction, grouped by kind, heaviest first. */
export function neighbourGroups(
  graph: SystemGraph,
  nodeId: string,
  direction: "out" | "in",
): NeighbourGroup[] {
  const byKind = new Map<SystemEdgeKind, SystemEdge[]>();
  for (const e of graph.edges) {
    if ((direction === "out" ? e.source : e.target) !== nodeId) continue;
    const list = byKind.get(e.kind) ?? [];
    list.push(e);
    byKind.set(e.kind, list);
  }
  const groups = [...byKind.entries()].map(([kind, edges]) => ({
    kind,
    edges: edges.sort((a, b) => b.weight - a.weight),
  }));
  // Structural before behavioral, then by how much rides on the group.
  groups.sort(
    (a, b) =>
      Number(a.kind === "co_change") - Number(b.kind === "co_change") ||
      b.edges.reduce((s, e) => s + e.weight, 0) - a.edges.reduce((s, e) => s + e.weight, 0),
  );
  return groups;
}

export function nodeName(graph: SystemGraph, id: string): string {
  return graph.nodes.find((n) => n.id === id)?.name ?? id;
}

/** Where a service lives: "repo / service/path", or "repo repository" for a repo root. */
export function nodeLocation(node: Pick<SystemNode, "repo" | "service_path">): string {
  return node.service_path ? `${node.repo} / ${node.service_path}` : `${node.repo} repository`;
}

// ---------------------------------------------------------------------------
// Shared wording.
// ---------------------------------------------------------------------------

export function plural(n: number, one: string, many: string): string {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}

/** Why a consumer matched no provider, as the tail of "N calls ...". */
const UNMATCHED_REASON: Record<string, string> = {
  no_provider: "have no provider anywhere",
  external_host: "call a third-party host",
  internal_only: "stay inside their own service",
  unlinked: "have a provider that did not link",
};

/** "54 have no provider anywhere, 26 call a third-party host", largest first. */
export function unmatchedReasonList(byReason: Record<string, number>): string {
  return Object.entries(byReason)
    .sort((a, b) => b[1] - a[1])
    .map(([r, n]) => `${n.toLocaleString()} ${UNMATCHED_REASON[r] ?? r}`)
    .join(", ");
}

// ---------------------------------------------------------------------------
// Selections against the drawn graph.
// ---------------------------------------------------------------------------

export type ViewSelectionFix = { kind: "show-edge-kind"; edgeKind: SystemEdgeKind } | { kind: "expand" } | null;

/**
 * Lists below the map, and the URL, name raw service and edge ids. The drawn
 * graph may be collapsed to repositories or missing a hidden edge kind. Maps
 * the selection onto what is drawn, or says what the map must change to draw
 * it: show a hidden kind, or return to the service view for an edge inside
 * one repository.
 */
export function resolveViewSelection(
  selection: { type: "node" | "edge"; id: string } | null,
  raw: SystemGraph,
  view: SystemGraph,
  collapsed: boolean,
): { selection: { type: "node" | "edge"; id: string } | null; fix: ViewSelectionFix } {
  if (!selection) return { selection: null, fix: null };
  const drawn =
    selection.type === "node"
      ? view.nodes.some((n) => n.id === selection.id)
      : view.edges.some((e) => e.id === selection.id);
  if (drawn) return { selection, fix: null };

  const repoOf = (id: string) => raw.nodes.find((n) => n.id === id)?.repo;
  if (selection.type === "node") {
    const repo = collapsed ? repoOf(selection.id) : undefined;
    return { selection: repo ? { type: "node", id: repo } : null, fix: null };
  }

  const edge = raw.edges.find((e) => e.id === selection.id);
  if (!edge) {
    // A collapsed id whose kind is hidden.
    const kind = selection.id.split("::").pop() as SystemEdgeKind | undefined;
    const known = kind && raw.edges.some((e) => e.kind === kind);
    return { selection: null, fix: collapsed && known ? { kind: "show-edge-kind", edgeKind: kind } : null };
  }
  if (!collapsed) return { selection: null, fix: { kind: "show-edge-kind", edgeKind: edge.kind } };
  const src = repoOf(edge.source);
  const tgt = repoOf(edge.target);
  if (!src || !tgt || src === tgt) return { selection: null, fix: { kind: "expand" } };
  const merged = `${src}->${tgt}::${edge.kind}`;
  return view.edges.some((e) => e.id === merged)
    ? { selection: { type: "edge", id: merged }, fix: null }
    : { selection: null, fix: { kind: "show-edge-kind", edgeKind: edge.kind } };
}
