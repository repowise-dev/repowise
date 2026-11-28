/**
 * Map search as data: services and groups matched over the loaded graph with
 * Fuse (the Architecture graph's search, `use-graph-search.ts`), routes from
 * contract links the host loads, resolved to the services that serve and call
 * them. A pick is a level change: a service is focused, a group opened, a
 * route compared between its top caller and its provider. Pure, no React.
 */

import Fuse from "fuse.js";
import type { SystemGraph, WorkspaceContractLinkEntry } from "@repowise-dev/types";
import { serviceResolver, type ServiceResolver } from "./system-map-model";
import { compareServices, focusService, openContainer, type MapViewState } from "./view-state";

/** The link fields a route hit reads; the contracts endpoint's link rows fit. */
export type SystemMapRouteLink = Pick<
  WorkspaceContractLinkEntry,
  "contract_id" | "contract_type" | "provider_repo" | "provider_file" | "consumer_repo" | "consumer_file"
>;

/** Loads the contract links matching a query (every term must match), e.g. `/api/workspace/contracts?q=`. */
export type SystemMapRouteLoader = (query: string) => Promise<readonly SystemMapRouteLink[]>;

export type SearchHit =
  | { kind: "service"; id: string; label: string; detail: string }
  | { kind: "group"; id: string; label: string; detail: string }
  | {
      kind: "route";
      id: string;
      label: string;
      contractType: string;
      provider: string;
      /** The service that calls the route most; null when only the provider's own service does. */
      consumer: string | null;
      /** Other calling services beyond `consumer`. */
      otherConsumers: number;
    };

interface Entry {
  id: string;
  label: string;
  detail: string;
}

export interface MapSearchIndex {
  services: Fuse<Entry>;
  groups: Fuse<Entry>;
  /** Owner of a file, kept only when it is a service on the map. */
  resolve: (repo: string, file: string) => string | null;
}

/** Search results per section, at most this many each. */
export const SEARCH_LIMIT = 6;
/** Below this many characters nothing is searched; one letter matches most of an estate. */
export const SEARCH_MIN_CHARS = 2;

const byLabel = (a: Entry, b: Entry) => (a.label < b.label ? -1 : a.label > b.label ? 1 : a.id < b.id ? -1 : 1);

/**
 * One index per graph. Entries are fed in label order, so equal scores keep
 * a stable, alphabetical order. The name weighs most; the id and repo let
 * `ordering.api::src` or a repo alias find a service too.
 */
export function buildSearchIndex(graph: Pick<SystemGraph, "nodes" | "groups">): MapSearchIndex {
  const services = graph.nodes
    .map((n) => ({ id: n.id, label: n.name, detail: n.service_path ? `${n.repo}/${n.service_path}` : n.repo }))
    .sort(byLabel);
  const groups = (graph.groups ?? [])
    .map((g) => ({ id: g.id, label: g.label, detail: `${g.member_count} services` }))
    .sort(byLabel);
  const options = {
    keys: [
      { name: "label", weight: 2 },
      { name: "id", weight: 1 },
      { name: "detail", weight: 1 },
    ],
    threshold: 0.4,
    ignoreLocation: true,
  };
  return {
    services: new Fuse(services, options),
    groups: new Fuse(groups, options),
    resolve: onMap(serviceResolver(graph), new Set(graph.nodes.map((n) => n.id))),
  };
}

function onMap(resolve: ServiceResolver, known: ReadonlySet<string>) {
  return (repo: string, file: string) => {
    const id = resolve(repo, file);
    return known.has(id) ? id : null;
  };
}

/** Services, then groups, best match first in each. */
export function searchMap(index: MapSearchIndex, query: string, limit = SEARCH_LIMIT): SearchHit[] {
  const q = query.trim();
  if (q.length < SEARCH_MIN_CHARS) return [];
  const services = index.services.search(q, { limit }).map(({ item }) => ({ kind: "service" as const, ...item }));
  const groups = index.groups.search(q, { limit }).map(({ item }) => ({ kind: "group" as const, ...item }));
  return [...services, ...groups];
}

/**
 * Route hits from contract links: one per (route, providing service), most
 * calling links first. The top caller is the service with the most links to
 * it, ties by id; a caller inside the provider's own service is not one, and
 * a side that resolves to no service on the map is dropped.
 */
export function routeHits(
  links: readonly SystemMapRouteLink[],
  resolve: MapSearchIndex["resolve"],
  limit = SEARCH_LIMIT,
): SearchHit[] {
  const routes = new Map<string, { link: SystemMapRouteLink; provider: string; callers: Map<string, number>; links: number }>();
  for (const link of links) {
    const provider = resolve(link.provider_repo, link.provider_file);
    if (!provider) continue;
    const key = `${link.contract_id}\u0000${provider}`;
    const row = routes.get(key) ?? { link, provider, callers: new Map<string, number>(), links: 0 };
    routes.set(key, row);
    row.links += 1;
    const consumer = resolve(link.consumer_repo, link.consumer_file);
    if (consumer && consumer !== provider) row.callers.set(consumer, (row.callers.get(consumer) ?? 0) + 1);
  }
  return [...routes.entries()]
    .sort(([ka, a], [kb, b]) => b.links - a.links || (ka < kb ? -1 : 1))
    .slice(0, limit)
    .map(([key, r]) => {
      const callers = [...r.callers.entries()].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1));
      return {
        kind: "route" as const,
        id: key,
        label: r.link.contract_id,
        contractType: r.link.contract_type,
        provider: r.provider,
        consumer: callers[0]?.[0] ?? null,
        otherConsumers: Math.max(callers.length - 1, 0),
      };
    });
}

/** The level a pick lands on. */
export function pickToView(view: MapViewState, hit: SearchHit): MapViewState {
  if (hit.kind === "service") return focusService(view, hit.id);
  if (hit.kind === "group") return openContainer({ ...view, focus: null, between: null }, hit.id);
  return hit.consumer ? compareServices({ ...view, focus: null }, hit.consumer, hit.provider) : focusService(view, hit.provider);
}
