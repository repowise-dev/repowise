/**
 * Loads every payload the Health dashboard renders in one pass and refetches
 * the whole set when the index moves under the panel. Webviews never fetch:
 * each call is an RPC the host serves from its shared cache and api-client.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import type {
  ChurnComplexityResponse,
  HealthOverviewResponse,
  HealthMapFeed,
  HealthTrendResponse,
} from "@repowise-dev/types/health";
import type { WebviewHost } from "../../runtime/rpc";

/** Files pulled for the map: biggest first, capped so the galaxy stays legible. */
const MAP_FILE_LIMIT = 2000;
/** Overview + trend history windows the lede and trend section read. */
const OVERVIEW_LIMIT = 25;
const TREND_LIMIT = 20;
/**
 * Churn points pulled for the churn lens. Deliberately larger than
 * `MAP_FILE_LIMIT`: the map ranks by NLOC and churn-complexity ranks by
 * `commit_count × max_ccn`, so the two windows do not hold the same files, and
 * a short window leaves mapped files painted as the key's "no data" swatch for
 * data that exists. A repo past this ceiling degrades to that partial join
 * rather than breaking.
 */
const CHURN_POINT_LIMIT = 5000;

export interface DashboardData {
  overview: HealthOverviewResponse;
  files: HealthMapFeed;
  trend: HealthTrendResponse;
}

export interface DashboardState {
  data: DashboardData | null;
  error: string | null;
  loading: boolean;
}

/**
 * Fetch the three health payloads together. `refreshToken` is the only
 * dependency: it changes on first mount (0) and whenever the host reports the
 * index moved, so the effect re-runs and re-pulls the set.
 */
export function useDashboardData(host: WebviewHost, refreshToken: number): DashboardState {
  const [state, setState] = useState<DashboardState>({
    data: null,
    error: null,
    loading: true,
  });

  useEffect(() => {
    let cancelled = false;
    setState((prev) => ({ ...prev, loading: true, error: null }));

    Promise.all([
      host.api.healthOverview(OVERVIEW_LIMIT),
      host.api.healthMap({ cap: MAP_FILE_LIMIT }),
      host.api.healthTrend(TREND_LIMIT),
    ])
      .then(([overview, files, trend]) => {
        if (cancelled) return;
        setState({ data: { overview, files, trend }, error: null, loading: false });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : "Could not load health data.";
        setState({ data: null, error: message, loading: false });
      });

    return () => {
      cancelled = true;
    };
  }, [host, refreshToken]);

  return state;
}

/**
 * Churn percentiles joined onto the map rows, for the churn lens only.
 *
 * A second request the other three lenses do not need, so it fires the first
 * time the lens is selected and is then held.
 *
 * Until it lands every node paints the key's "no data" swatch, because the map
 * payload carries no `churn_percentile` at all. That is the whole reason the
 * legend has to be told it is loading: an all-grey field under a full churn key
 * asserts "no churn anywhere", which is a claim rather than a wait. So the
 * pending flag is derived from the data rather than from an effect — a
 * `useState` set inside `useEffect` is written after paint, which would render
 * one frame of the complete key over a field that has nothing in it yet.
 */
export function useChurnLens(
  host: WebviewHost,
  files: HealthMapFeed,
  wanted: boolean,
): { files: HealthMapFeed; loading: boolean; failed: boolean } {
  const [churn, setChurn] = useState<ChurnComplexityResponse | null>(null);
  const [failed, setFailed] = useState(false);
  // Unmount, not lens-change. Cancelling when the reader switches away would
  // throw away a 5,000-point response mid-flight and refetch the whole thing
  // on the next click back, which is the one payload here worth not paying for
  // twice. Nothing reads the result while another lens is selected.
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  useEffect(() => {
    if (!wanted || churn || failed) return;
    host.api
      .churnComplexity(CHURN_POINT_LIMIT)
      .then((r) => {
        if (alive.current) setChurn(r);
      })
      // The lens degrades to "could not load" rather than to a silent
      // all-clear; the other lenses and the rest of the panel are unaffected.
      .catch(() => {
        if (alive.current) setFailed(true);
      });
  }, [host, wanted, churn, failed]);

  const joined = useMemo(() => {
    if (!churn) return files;
    const byPath = new Map(churn.points.map((p) => [p.file_path, p.churn_percentile]));
    return {
      ...files,
      files: files.files.map((file) => ({
        ...file,
        churn_percentile: byPath.get(file.file_path) ?? null,
      })),
    };
  }, [files, churn]);

  return { files: joined, loading: wanted && !churn && !failed, failed: wanted && failed };
}
