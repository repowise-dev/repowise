/** Areas: directory rollups at any depth (`/areas/tree`, `/areas/{path}`). */
import type { AreaDetail, AreaTree, AreaTreeQuery, MapHoldQuery } from "@repowise-dev/types/areas";
import { apiGet } from "./client";

export type {
  AreaDeltaWindow,
  AreaDetail,
  AreaFileRow,
  AreaFinding,
  AreaHeld,
  AreaNode,
  AreaOwner,
  AreaTree,
  AreaTreeQuery,
  MapHoldQuery,
} from "@repowise-dev/types/areas";

/** The hold parameters as query values; nothing when every file is read. */
export function holdParams(opts: MapHoldQuery = {}): Record<string, string | undefined> {
  if (opts.hold !== "map") return {};
  // Sorted, so two orders of the same toggles are one request and one cache entry.
  return { hold: "map", include: opts.include?.length ? [...opts.include].sort().join(",") : undefined };
}

/** The root area's rollup plus every directory up to `depth` levels below it. */
export async function getAreaTree(repoId: string, opts: AreaTreeQuery = {}): Promise<AreaTree> {
  return apiGet<AreaTree>(`/api/repos/${repoId}/areas/tree`, {
    root: opts.root || undefined,
    depth: opts.depth,
    ...holdParams(opts),
  });
}

/** One area's card; `""` is the repository root. */
export async function getAreaDetail(repoId: string, area: string, opts: MapHoldQuery = {}): Promise<AreaDetail> {
  const path = area.split("/").filter(Boolean).map(encodeURIComponent).join("/");
  return apiGet<AreaDetail>(
    path ? `/api/repos/${repoId}/areas/${path}` : `/api/repos/${repoId}/areas`,
    holdParams(opts),
  );
}
