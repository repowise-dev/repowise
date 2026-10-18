/** Areas: directory rollups at any depth (`/areas/tree`, `/areas/{path}`). */
import type { AreaDetail, AreaTree, AreaTreeQuery } from "@repowise-dev/types/areas";
import { apiGet } from "./client";

export type {
  AreaDeltaWindow,
  AreaDetail,
  AreaFileRow,
  AreaFinding,
  AreaNode,
  AreaOwner,
  AreaTree,
  AreaTreeQuery,
} from "@repowise-dev/types/areas";

/** The root area's rollup plus every directory up to `depth` levels below it. */
export async function getAreaTree(repoId: string, opts: AreaTreeQuery = {}): Promise<AreaTree> {
  return apiGet<AreaTree>(`/api/repos/${repoId}/areas/tree`, {
    root: opts.root || undefined,
    depth: opts.depth,
  });
}

/** One area's card; `""` is the repository root. */
export async function getAreaDetail(repoId: string, area: string): Promise<AreaDetail> {
  const path = area.split("/").filter(Boolean).map(encodeURIComponent).join("/");
  return apiGet<AreaDetail>(path ? `/api/repos/${repoId}/areas/${path}` : `/api/repos/${repoId}/areas`);
}
