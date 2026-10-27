"use client";

/**
 * Pages of routes between two services through the host's loader. The first
 * request reads the totals per kind and direction under the filter; each
 * kind the map's families keep then loads its own pages, so paging is exact
 * per kind and a hidden family costs no row requests. State resets when the
 * pair or the filter changes; a reply for an earlier pair or filter is
 * dropped. The edge drawer's smaller read (one kind, one direction, no
 * paging) lives here too, so both reads share one loader contract.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import type { ServiceLinkEntry, ServiceLinksBetween } from "@repowise-dev/types/workspace";
import type { EdgeFamily } from "./edge-families";
import { ROUTES_PAGE, isStale, planRoutes, type LoadLinksBetween, type RoutesPlan } from "./routes-between";

export interface KindRows {
  rows: ServiceLinkEntry[];
  loading: boolean;
  error: boolean;
}

export interface RoutesBetweenState {
  /** No loader: the host cannot list routes here. */
  unavailable: boolean;
  loading: boolean;
  error: boolean;
  plan: RoutesPlan | null;
  stale: boolean;
  byKind: Readonly<Record<string, KindRows>>;
  loadMore: (kind: string) => void;
}

export interface RoutesBetweenArgs {
  load: LoadLinksBetween | undefined;
  a: string;
  b: string;
  families: ReadonlySet<EdgeFamily>;
  q: string;
}

/** One pair and filter: what every request and every reply is keyed by. */
interface Query {
  load: LoadLinksBetween | undefined;
  a: string;
  b: string;
  q: string;
  key: string;
}

interface Totals {
  key: string;
  page: ServiceLinksBetween | null;
}

const EMPTY_KIND: KindRows = { rows: [], loading: true, error: false };

const withQ = (q: string) => (q ? { q } : {});

/** The totals request for the current query; null until its reply lands. */
function useRouteTotals({ load, a, b, q, key }: Query): Totals | null {
  const [totals, setTotals] = useState<Totals | null>(null);
  useEffect(() => {
    if (!load) return;
    let cancelled = false;
    load(a, b, { ...withQ(q), limit: 1 }).then(
      (page) => !cancelled && setTotals({ key, page }),
      () => !cancelled && setTotals({ key, page: null }),
    );
    return () => {
      cancelled = true;
    };
  }, [load, a, b, q, key]);
  return totals?.key === key ? totals : null;
}

/** Rows per kind for the current query, and a fetch for one page of one kind. */
function useKindPages({ load, a, b, q, key }: Query) {
  const [pages, setPages] = useState<{ key: string; byKind: Record<string, KindRows> }>({ key, byKind: {} });
  const byKind = pages.key === key ? pages.byKind : {};

  const fetchPage = useCallback(
    (kind: string, offset: number) => {
      if (!load) return;
      // A reply for an earlier pair or filter is dropped, never written over the current one.
      const patch = (next: (prior: KindRows) => KindRows) =>
        setPages((p) => (p.key !== key ? p : { key, byKind: { ...p.byKind, [kind]: next(p.byKind[kind] ?? EMPTY_KIND) } }));
      setPages((p) => {
        const base = p.key === key ? p.byKind : {};
        return { key, byKind: { ...base, [kind]: { ...(base[kind] ?? EMPTY_KIND), loading: true, error: false } } };
      });
      load(a, b, { kind, ...withQ(q), limit: ROUTES_PAGE, offset }).then(
        (page) => patch((prior) => ({ rows: [...prior.rows.slice(0, offset), ...page.links], loading: false, error: false })),
        () => patch((prior) => ({ ...prior, loading: false, error: true })),
      );
    },
    [load, a, b, q, key],
  );
  return { byKind, fetchPage };
}

/** The totals and the first page of each kept kind, for one pair and filter. */
export function useRoutesBetween({ load, a, b, families, q }: RoutesBetweenArgs): RoutesBetweenState {
  const query: Query = { load, a, b, q, key: `${a}\u0000${b}\u0000${q}` };
  const totals = useRouteTotals(query);
  const { byKind, fetchPage } = useKindPages(query);
  const page = totals?.page ?? null;
  const plan = useMemo(() => (page ? planRoutes(page.totals, families) : null), [page, families]);

  // First page of every kept kind not asked for yet (a family turned on later loads then).
  const missingKey = (plan?.sections ?? [])
    .map((s) => s.kind)
    .filter((k) => !byKind[k])
    .join(",");
  useEffect(() => {
    if (missingKey) for (const kind of missingKey.split(",")) fetchPage(kind, 0);
  }, [missingKey, fetchPage]);

  const loadMore = useCallback(
    (kind: string) => {
      const prior = byKind[kind];
      // After an error this retries the page that failed.
      if (prior && !prior.loading) fetchPage(kind, prior.rows.length);
    },
    [byKind, fetchPage],
  );

  return {
    unavailable: !load,
    loading: Boolean(load) && !totals,
    error: Boolean(totals && !page),
    plan,
    stale: isStale(page),
    byKind,
    loadMore,
  };
}

export interface EdgeRoutes {
  loading: boolean;
  error: boolean;
  /** Rows of the edge's kind running source to target, at most `limit`. */
  rows: ServiceLinkEntry[];
  /** Rows of that kind and direction on the server. */
  total: number;
  stale: boolean;
}

const EDGE_LOADING: EdgeRoutes = { loading: true, error: false, rows: [], total: 0, stale: false };
const EDGE_FAILED: EdgeRoutes = { loading: false, error: true, rows: [], total: 0, stale: false };

/** One edge's rows from a page of its pair: its kind, source to target. */
function edgeRoutesFrom(page: ServiceLinksBetween, kind: string): EdgeRoutes {
  const total = page.totals.find((t) => t.kind === kind && t.direction === "a_to_b")?.count ?? 0;
  return { loading: false, error: false, rows: page.links.filter((r) => r.direction === "a_to_b"), total, stale: isStale(page) };
}

/**
 * The routes behind one edge: its kind, from its source (the consumer) to its
 * target. Rows come ordered by direction, so the first page holds this
 * direction's rows first. Null without a loader or without an edge to read
 * (co-change evidence lives on the edge itself).
 */
export function useEdgeRoutes(
  load: LoadLinksBetween | undefined,
  edge: { source: string; target: string; kind: string } | null,
  limit: number,
): EdgeRoutes | null {
  const [state, setState] = useState<{ key: string; value: EdgeRoutes } | null>(null);
  const source = edge?.source ?? "";
  const target = edge?.target ?? "";
  const kind = edge?.kind ?? "";
  const key = `${source}\u0000${target}\u0000${kind}`;
  useEffect(() => {
    if (!load || !source) return;
    let cancelled = false;
    load(source, target, { kind, limit }).then(
      (page) => !cancelled && setState({ key, value: edgeRoutesFrom(page, kind) }),
      () => !cancelled && setState({ key, value: EDGE_FAILED }),
    );
    return () => {
      cancelled = true;
    };
  }, [load, source, target, kind, key, limit]);
  if (!load || !edge) return null;
  return state?.key === key ? state.value : EDGE_LOADING;
}
