/**
 * Loads every payload the Health dashboard renders in one pass and refetches
 * the whole set when the index moves under the panel. Webviews never fetch:
 * each call is an RPC the host serves from its shared cache and api-client.
 */

import { useEffect, useState } from "react";
import type {
  ChurnComplexityResponse,
  HealthOverviewResponse,
  HealthFilesResponse,
  HealthTrendResponse,
} from "@repowise-dev/types/health";
import type { WebviewHost } from "../../runtime/rpc";

/** Files pulled for the map: biggest first, capped so the galaxy stays legible. */
const MAP_FILE_LIMIT = 2000;
/** Overview + trend history windows the KPI header reads. */
const OVERVIEW_LIMIT = 25;
const TREND_LIMIT = 20;
/** Points for the churn-versus-complexity scatter. */
const QUADRANT_LIMIT = 400;

export interface DashboardData {
  overview: HealthOverviewResponse;
  files: HealthFilesResponse;
  trend: HealthTrendResponse;
  churn: ChurnComplexityResponse;
}

export interface DashboardState {
  data: DashboardData | null;
  error: string | null;
  loading: boolean;
}

/**
 * Fetch the four health payloads together. `refreshToken` is the only
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
      host.api.healthFiles({ limit: MAP_FILE_LIMIT, sort: "nloc", order: "desc" }),
      host.api.healthTrend(TREND_LIMIT),
      host.api.churnComplexity(QUADRANT_LIMIT),
    ])
      .then(([overview, files, trend, churn]) => {
        if (cancelled) return;
        setState({ data: { overview, files, trend, churn }, error: null, loading: false });
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
