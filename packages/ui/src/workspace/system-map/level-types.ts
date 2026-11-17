/**
 * Shapes for the System Map levels model: the narrow graph it reads, the view
 * state it is driven by, and the drawn graph it returns. Plain data, no React.
 *
 * Input is deliberately narrower than `SystemGraph`: a full `SystemGraph`
 * satisfies it, and so does a lean payload that dropped `contract_refs` and
 * `diagnostics`. `group` / `groups` are optional, and a graph without them is
 * drawn as one flat estate.
 */

import type { SystemEdge, SystemNode } from "@repowise-dev/types";
import type { EdgeFamily } from "./edge-kinds";

/** A service as the levels model reads it. `group` is the id of the system it belongs to, when the server grouped the estate. */
export type LevelsNode = Pick<SystemNode, "id" | "name" | "repo"> & { group?: string | null };

/**
 * An edge as the levels model reads it. `source` depends on / calls `target`.
 * `family` is core's filter family when the payload carries it; the kind
 * registry answers for payloads that do not.
 */
export type LevelsEdge = Pick<SystemEdge, "id" | "source" | "target" | "kind" | "weight" | "strategy" | "family">;

/** Summary of one group, when the graph carries one. Only `id` is required. */
export interface LevelsGroupMeta {
  id: string;
  /** Display name; the id is used when absent. */
  label?: string | null;
  /** Where the group came from (declared tag, name family, linkage). Passed through for the card. */
  source?: string | null;
}

export interface LevelsGraph {
  nodes: readonly LevelsNode[];
  edges: readonly LevelsEdge[];
  /** Present and non-empty only when the server grouped the estate; absent means draw it flat. */
  groups?: readonly LevelsGroupMeta[] | null | undefined;
}

/** At most this much is drawn at once; the rest is aggregated and reported. */
export interface DrawBudget {
  nodes: number;
  edges: number;
}

/** What the reader asked to see. Ids that name nothing in the graph are ignored. */
export interface LevelViewState {
  /** Group ids opened in place (L1). */
  open?: Iterable<string>;
  /** Open the "Not linked at runtime" tray into its service cards. */
  openTray?: boolean;
  /** Service id to focus (L2). */
  focus?: string | null;
  /** Rings of callers and dependencies around the focus. */
  hops?: 1 | 2;
  /** Two service ids to show the links between (L3). Wins over `focus`. */
  between?: { a: string; b: string } | null;
  /** Edge families drawn; defaults to Runtime and Data. */
  families?: ReadonlySet<EdgeFamily>;
}

export type MapLevel = "estate" | "group" | "focus" | "between";

/** The links one drawn connector or one column entry stands for. */
export interface LinkSummary {
  /** Underlying links (sum of edge weights). */
  links: number;
  /** System edges aggregated. */
  edges: number;
  /** Links per edge kind. */
  kinds: Record<string, number>;
  /** Links a gateway's route config made, named "via gateway" on hover. */
  gatewayLinks: number;
  edgeIds: string[];
}

/** A group drawn as a card (closed) or as the container of its service cards (open). */
export interface DrawnGroupNode {
  type: "group";
  id: string;
  groupId: string;
  label: string;
  source: string | null;
  open: boolean;
  serviceIds: string[];
  repoCount: number;
  /** Links drawn into, out of, and inside the group, in the families shown. */
  linksIn: number;
  linksOut: number;
  linksInside: number;
}

/** A service card. `id` is the service id except where one service is drawn twice (focus columns). */
export interface DrawnServiceNode {
  type: "service";
  id: string;
  serviceId: string;
  /** Id of the open group or tray that contains the card. */
  parentId: string | null;
  /** Focus level only: the column the card sits in. */
  column?: FocusColumnId | "focus";
  /** Focus level only: the links between this service and its neighbour column. */
  links?: LinkSummary;
  /** Focus level only: the service both calls and is called by the focus. */
  bothWays?: boolean;
}

/** Services with no Runtime or Data link, as one card (closed) or a container (open). */
export interface DrawnTrayNode {
  type: "tray";
  id: string;
  open: boolean;
  serviceIds: string[];
}

/** "+N more": what the draw budget folded in one container. */
export interface DrawnMoreNode {
  type: "more";
  id: string;
  parentId: string | null;
  serviceIds: string[];
  /** Group cards folded into it (estate level only). */
  groupIds: string[];
}

export type DrawnNode = DrawnGroupNode | DrawnServiceNode | DrawnTrayNode | DrawnMoreNode;

/** One system edge between two drawn service cards. */
export interface DrawnSingleEdge {
  type: "edge";
  id: string;
  source: string;
  target: string;
  edgeId: string;
}

/** Every link between two drawn nodes when at least one of them aggregates (or on focus columns). */
export interface DrawnBand {
  type: "band";
  id: string;
  source: string;
  target: string;
  summary: LinkSummary;
}

export type DrawnEdge = DrawnSingleEdge | DrawnBand;

export type FocusColumnId = "callers2" | "callers" | "dependencies" | "dependencies2";

/** What the draw budget left out. Null on a view that fits. */
export interface TruncationReport {
  budget: DrawBudget;
  /** Services drawn only inside a "+N more" node. */
  foldedServices: number;
  /** Group cards drawn only inside a "+N more" node. */
  foldedGroups: number;
  /** Connectors not drawn because the edge budget was spent, weakest first. */
  droppedEdges: number;
  droppedLinks: number;
}
