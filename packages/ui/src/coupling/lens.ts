/**
 * The coupling lens for the area map: how strongly each area or file changes
 * together with code it does not depend on.
 *
 * Pure. Area figures come from `GET /coupling/areas` at the depth and root the
 * map is drawn at, so no path arithmetic happens here; file figures come from a
 * `GET /coupling` edge list. Intensity is normalised to 0-1 against the
 * strongest item, with the raw counts beside it so a legend can name them.
 * Ceiling: file intensity is only as complete as the edge list passed in
 * (capped at the route's 1,000); the map lane's lens adapter should say so.
 */
import type { CouplingAreaSummary, CouplingEdge } from "@repowise-dev/types/coupling";

export interface CouplingIntensity {
  /** 0-1 against the most coupled item in the same set. */
  intensity: number;
  /** File pairs counted. */
  pairs: number;
  /** Of those, pairs no dependency explains. */
  unexplained: number;
}

export interface CouplingLensOptions {
  /** Measure by unexplained pairs (default), the actionable reading, or by every pair. */
  measure?: "unexplained" | "all";
}

function normalise(
  tallies: Map<string, { pairs: number; unexplained: number }>,
  measure: "unexplained" | "all",
): Map<string, CouplingIntensity> {
  const value = (t: { pairs: number; unexplained: number }) =>
    measure === "all" ? t.pairs : t.unexplained;
  let max = 0;
  for (const t of tallies.values()) max = Math.max(max, value(t));
  const out = new Map<string, CouplingIntensity>();
  for (const [key, t] of tallies) {
    out.set(key, { intensity: max > 0 ? value(t) / max : 0, ...t });
  }
  return out;
}

/** Per-area intensity, keyed by area path. */
export function couplingIntensityByArea(
  areas: readonly CouplingAreaSummary[],
  { measure = "unexplained" }: CouplingLensOptions = {},
): Map<string, CouplingIntensity> {
  const tallies = new Map<string, { pairs: number; unexplained: number }>();
  for (const a of areas) tallies.set(a.path, { pairs: a.pairs, unexplained: a.unexplained_pairs });
  return normalise(tallies, measure);
}

/** Per-file intensity, keyed by file path; each pair counts for both files. */
export function couplingIntensityByFile(
  edges: readonly CouplingEdge[],
  { measure = "unexplained" }: CouplingLensOptions = {},
): Map<string, CouplingIntensity> {
  const tallies = new Map<string, { pairs: number; unexplained: number }>();
  for (const e of edges) {
    const hidden = e.structural === "unexplained" ? 1 : 0;
    for (const path of [e.source, e.target]) {
      const t = tallies.get(path) ?? { pairs: 0, unexplained: 0 };
      t.pairs += 1;
      t.unexplained += hidden;
      tallies.set(path, t);
    }
  }
  return normalise(tallies, measure);
}
