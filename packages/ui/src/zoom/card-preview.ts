/**
 * A container card's "contains" preview: its top children as a short list
 * and its files split by health band. Pure, browser-free and unit-tested;
 * `card-text.ts` paints it in the space a large card would otherwise leave
 * blank, which also hints at what a dive will show.
 *
 * Built once per node object and cached (the scene keeps nodes stable), so a
 * frame only reads it.
 */

import { bandForScore, type HealthBand } from "@repowise-dev/types/health";

import { titleMode } from "./card-content";
import type { WrapMode } from "./text";
import type { ZoomNode } from "./types";

/** Most children a preview lists; a card shows as many as fit. */
export const PREVIEW_MAX_ITEMS = 12;

export interface PreviewItem {
  name: string;
  mode: WrapMode;
  /** Health band of the child, or null when unscored. */
  band: HealthBand | null;
  /** Files under a container child ("" for a file), right-aligned. */
  count: string;
}

/** Composition bar order, worst first; Excellent folds into Good (one green). */
export const COMPOSITION_BANDS = ["at_risk", "needs_work", "fair", "good"] as const;

export interface CardPreview {
  /** Top children by `sibling_rank`, at most `PREVIEW_MAX_ITEMS`. */
  items: readonly PreviewItem[];
  /** How many children the preview lists in all (items may be fewer). */
  total: number;
  /**
   * Files per band in `COMPOSITION_BANDS` order, then unscored, over the
   * subtree. A child the map did not expand counts all its files in its own
   * band.
   */
  bands: readonly number[];
  /** Sum of `bands`. */
  files: number;
}

type NodeIndex = ReadonlyMap<string, ZoomNode>;

// Keyed by node object: a new zoom map brings new node objects, and a rebuild
// for a new canvas shape reuses the same ones with the same children, so an
// entry never outlives the tree it was built from. (Expanding a node's
// `children` in place would need these cleared.)
const previews = new WeakMap<ZoomNode, CardPreview | null>();
const compositions = new WeakMap<ZoomNode, readonly number[]>();

function bandSlot(score: number | null): number {
  if (score === null) return COMPOSITION_BANDS.length;
  const band = bandForScore(score);
  return band === "excellent" ? COMPOSITION_BANDS.indexOf("good") : COMPOSITION_BANDS.indexOf(band);
}

function servedChildren(nodes: NodeIndex, node: ZoomNode): ZoomNode[] {
  const out: ZoomNode[] = [];
  for (const id of node.children) {
    const c = nodes.get(id);
    if (c) out.push(c);
  }
  return out;
}

/** Files per band over `node`'s subtree (memoised per node, so a tree is walked once). */
export function composition(nodes: NodeIndex, node: ZoomNode): readonly number[] {
  const hit = compositions.get(node);
  if (hit) return hit;
  const out = new Array<number>(COMPOSITION_BANDS.length + 1).fill(0);
  const kids = servedChildren(nodes, node);
  if (kids.length === 0) {
    out[bandSlot(node.health_score)]! += Math.max(1, node.metrics.file_count);
  } else {
    for (const kid of kids) {
      const sub = composition(nodes, kid);
      for (let i = 0; i < out.length; i++) out[i]! += sub[i]!;
    }
  }
  compositions.set(node, out);
  return out;
}

/**
 * The node whose children a card lists: the card's own, skipping a chain of
 * lone children (a group holding one folder lists that folder's contents).
 */
function listedParent(nodes: NodeIndex, node: ZoomNode): ZoomNode {
  let cur = node;
  for (;;) {
    const kids = servedChildren(nodes, cur);
    if (kids.length !== 1 || kids[0]!.children.length === 0) return cur;
    cur = kids[0]!;
  }
}

function item(child: ZoomNode): PreviewItem {
  const n = child.metrics.file_count;
  return {
    name: child.name,
    mode: titleMode(child.kind, child.name),
    band: child.health_score === null ? null : bandForScore(child.health_score),
    count: child.kind === "file" || n <= 0 ? "" : String(n),
  };
}

/** The preview for a container card, or null for a file or a node with no served children. */
export function cardPreview(nodes: NodeIndex, node: ZoomNode): CardPreview | null {
  const hit = previews.get(node);
  if (hit !== undefined) return hit;
  let out: CardPreview | null = null;
  if (node.kind !== "file") {
    const kids = servedChildren(nodes, listedParent(nodes, node));
    if (kids.length > 0) {
      kids.sort((a, b) => a.sibling_rank - b.sibling_rank || (a.id < b.id ? -1 : 1));
      const bands = composition(nodes, node);
      out = {
        items: kids.slice(0, PREVIEW_MAX_ITEMS).map(item),
        total: kids.length,
        bands,
        files: bands.reduce((s, v) => s + v, 0),
      };
    }
  }
  previews.set(node, out);
  return out;
}
