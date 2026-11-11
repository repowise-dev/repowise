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
  AreaHeld,
  AreaNode,
  AreaOwner,
  AreaTreeResponse as AreaTree,
} from "./generated/http.js";

/**
 * Which files a map read covers. `hold: "map"` leaves out the origins the
 * area map holds by default (the server names them in `AreaTree.held`);
 * `include` brings some of them back. Omitted: every file.
 */
export interface MapHoldQuery {
  hold?: "none" | "map";
  /** Held origins to bring back, e.g. `["test"]`. */
  include?: readonly string[];
}

export interface AreaTreeQuery extends MapHoldQuery {
  /** Area to unfold; the repository root by default. */
  root?: string;
  /** Levels below the root, 1-4. */
  depth?: number;
}
