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

interface Totals {
  key: string;
  page: ServiceLinksBetween | null;
  error: boolean;
}

const EMPTY_KIND: KindRows = { rows: [], loading: true, error: false };

/** The request totals and the first page of each kept kind, for one pair and filter. */
export function useRoutesBetween(
  load: LoadLinksBetween | undefined,
  a: string,
  b: string,
  families: ReadonlySet<EdgeFamily>,
  q: string,
): RoutesBetweenState {
  const key = `${a}\u0000${b}\u0000${q}`;
  const [totals, setTotals] = useState<Totals | null>(null);
  const [pages, setPages] = useState<{ key: string; byKind: Record<string, KindRows> }>({ key, byKind: {} });

  useEffect(() => {
    if (!load) return;
    let cancelled = false;
    setTotals(null);
    load(a, b, { ...(q ? { q } : {}), limit: 1 }).then(
      (page) => !cancelled && setTotals({ key, page, error: false }),
      () => !cancelled && setTotals({ key, page: null, error: true }),
    );
    return () => {
      cancelled = true;
    };
  }, [load, a, b, q, key]);

  const current = totals?.key === key ? totals : null;
  const plan = useMemo(() => (current?.page ? planRoutes(current.page.totals, families) : null), [current, families]);
  const byKind = pages.key === key ? pages.byKind : {};

  const fetchPage = useCallback(
    (kind: string, offset: number) => {
      if (!load) return;
      const mark = (patch: (prior: KindRows) => KindRows) =>
        setPages((p) => (p.key !== key ? p : { key, byKind: { ...p.byKind, [kind]: patch(p.byKind[kind] ?? EMPTY_KIND) } }));
      setPages((p) => {
        const base = p.key === key ? p.byKind : {};
        const prior = base[kind] ?? EMPTY_KIND;
        return { key, byKind: { ...base, [kind]: { ...prior, loading: true, error: false } } };
      });
      load(a, b, { kind, ...(q ? { q } : {}), limit: ROUTES_PAGE, offset }).then(
        (page) => mark((prior) => ({ rows: [...prior.rows.slice(0, offset), ...page.links], loading: false, error: false })),
        () => mark((prior) => ({ ...prior, loading: false, error: true })),
      );
    },
    [load, a, b, q, key],
  );

  // First page of every kept kind not asked for yet (a family turned on later loads then).
  const missing = (plan?.sections ?? []).map((s) => s.kind).filter((k) => !byKind[k]);
  const missingKey = missing.join(",");
  useEffect(() => {
    for (const kind of missingKey ? missingKey.split(",") : []) fetchPage(kind, 0);
  }, [missingKey, fetchPage]);

  const loadMore = useCallback(
    (kind: string) => {
      const prior = byKind[kind];
      if (prior && !prior.loading) fetchPage(kind, prior.rows.length);
    },
    [byKind, fetchPage],
  );

  return {
    unavailable: !load,
    loading: Boolean(load) && !current,
    error: Boolean(current?.error),
    plan,
    stale: isStale(current?.page),
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

/**
 * The routes behind one edge: its kind, from its source (the consumer) to its
 * target. Rows come ordered by direction, so the first page holds this
 * direction's rows first. Null without a loader or for an edge with no
 * routes to list (co-change rows live on the edge itself).
 */
export function useEdgeRoutes(
  load: LoadLinksBetween | undefined,
  edge: { source: string; target: string; kind: string } | null,
  limit: number,
): EdgeRoutes | null {
  const [state, setState] = useState<{ key: string; value: EdgeRoutes } | null>(null);
  const key = edge ? `${edge.source}\u0000${edge.target}\u0000${edge.kind}` : "";
  useEffect(() => {
    if (!load || !edge) return;
    let cancelled = false;
    load(edge.source, edge.target, { kind: edge.kind, limit }).then(
      (page) => {
        if (cancelled) return;
        const total = page.totals.find((t) => t.kind === edge.kind && t.direction === "a_to_b")?.count ?? 0;
        const rows = page.links.filter((r) => r.direction === "a_to_b");
        setState({ key, value: { loading: false, error: false, rows, total, stale: isStale(page) } });
      },
      () => !cancelled && setState({ key, value: { loading: false, error: true, rows: [], total: 0, stale: false } }),
    );
    return () => {
      cancelled = true;
    };
    // `edge` is keyed by `key`; a new object for the same edge is the same request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load, key, limit]);
  if (!load || !edge) return null;
  return state?.key === key ? state.value : { loading: true, error: false, rows: [], total: 0, stale: false };
}
