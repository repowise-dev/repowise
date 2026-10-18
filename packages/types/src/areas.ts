/**
 * Areas: any directory at any depth, rolled up from the stored analysis.
 *
 * Generated from the server's response models
 * (`packages/server/src/repowise/server/schemas/areas.py`) for
 * `GET /api/repos/{id}/areas/tree` and `GET /api/repos/{id}/areas/{path}`.
 * The repository root is the area `""`. Every figure covers the area's scored
 * files, complete rather than capped like the map's drawn field.
 */
export type {
  AreaDeltaWindow,
  AreaDetailResponse as AreaDetail,
  AreaFileRow,
  AreaFinding,
  AreaNode,
  AreaOwner,
  AreaTreeResponse as AreaTree,
} from "./generated/http.js";

export interface AreaTreeQuery {
  /** Area to unfold; the repository root by default. */
  root?: string;
  /** Levels below the root, 1-4. */
  depth?: number;
}
