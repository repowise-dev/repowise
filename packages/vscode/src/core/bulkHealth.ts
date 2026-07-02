import {
  listHealthFiles,
  type HealthFileMetric,
} from "@repowise-dev/api-client/code-health";
import type { RepowiseContext } from "./context";

/**
 * The per-file health scores both editor signals read. A lean projection of
 * `HealthFileMetric`: only the fields the decoration badge and status item
 * render, so callers never depend on the wider metric shape. Nullable score
 * fields stay nullable, since older index payloads predate the split.
 */
export interface FileScores {
  /** Overall surfaced score (equals `defect_score` until a blend decision). */
  score: number;
  defectScore: number | null;
  maintainabilityScore: number | null;
  performanceScore: number | null;
  nloc: number;
  module: string | null;
}

/** In-memory cache key for the bulk files payload; tag is the head commit. */
const CACHE_KEY = "health:files";

/** Server-enforced maximum page size for the files endpoint. */
const PAGE_LIMIT = 2000;

/**
 * In-flight fetches keyed by `repoId:tag`, so concurrent callers (both signals
 * reacting to the same ready/index event) share one request instead of racing
 * duplicate paginations. Cleared when the fetch settles.
 */
const inFlight = new Map<string, Promise<Map<string, FileScores> | null>>();

function toScores(metric: HealthFileMetric): FileScores {
  return {
    score: metric.score,
    defectScore: metric.defect_score ?? null,
    maintainabilityScore: metric.maintainability_score ?? null,
    performanceScore: metric.performance_score ?? null,
    nloc: metric.nloc,
    module: metric.module,
  };
}

/**
 * Pages the health files endpoint until every scored file is collected, keyed
 * by repo-relative forward-slash path. Returns null on any failure so callers
 * degrade quietly instead of throwing.
 */
async function fetchAll(
  ctx: RepowiseContext,
  repoId: string,
): Promise<Map<string, FileScores> | null> {
  try {
    const map = new Map<string, FileScores>();
    let offset = 0;
    for (;;) {
      const page = await listHealthFiles(repoId, { limit: PAGE_LIMIT, offset });
      for (const metric of page.files) {
        map.set(metric.file_path, toScores(metric));
      }
      offset += page.files.length;
      // Stop on an empty page (guards a mismatched total) or once the reported
      // total is covered.
      if (page.files.length === 0 || offset >= page.total) break;
    }
    return map;
  } catch (err) {
    ctx.log.debug(`bulk health fetch failed: ${String(err)}`);
    return null;
  }
}

/**
 * One bulk fetch of per-file scores, shared by the decoration and status
 * signals. Returns null (never throws) when not ready, no repo is resolved, or
 * the request fails. Served from the session cache when the head commit is
 * unchanged, and deduplicated while a fetch is in flight.
 */
export async function getBulkHealth(
  ctx: RepowiseContext,
): Promise<Map<string, FileScores> | null> {
  const repoId = ctx.repoId;
  if (ctx.getExtensionState() !== "ready" || !repoId) return null;

  const tag = ctx.repo?.head_commit ?? "";
  const cached = ctx.cache.get<Map<string, FileScores>>(repoId, CACHE_KEY, tag);
  if (cached) return cached;

  const dedupeKey = `${repoId}:${tag}`;
  const pending = inFlight.get(dedupeKey);
  if (pending) return pending;

  const request = fetchAll(ctx, repoId)
    .then((map) => {
      if (map) ctx.cache.set(repoId, CACHE_KEY, tag, map);
      return map;
    })
    .finally(() => {
      inFlight.delete(dedupeKey);
    });
  inFlight.set(dedupeKey, request);
  return request;
}
