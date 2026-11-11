/**
 * Positions for a drawn levels view. Focus is a fixed column grid (no ELK).
 * Every other level is laid out per scope (`layout-scopes.ts`): each open
 * container's interior first, then the root with those containers at their
 * fitted size. ELK runs in a worker when one can start, with
 * `considerModelOrder` and nodes in their previous order so a relayout keeps
 * neighbours in place. Scope layouts are cached by signature.
 *
 * Output follows React Flow: a card inside an open container is positioned
 * relative to it, and an open container carries its fitted width and height.
 */

import { computeC4Layout, type C4LayoutPosition } from "../../c4/layout/elk-c4-layout";
import { placeFocusColumns } from "./focus-columns";
import {
  LEVEL_NODE_SIZES,
  fitContainer,
  orderByPrevious,
  planLevelLayout,
  planSignature,
  scopeSignature,
  type FittedContainer,
  type LayoutScope,
  type LevelLayoutPlan,
  type LevelNodeSizes,
} from "./layout-scopes";
import type { LevelView } from "./levels";
import type { LevelsEdge } from "./level-types";

export type LevelPositions = Map<string, C4LayoutPosition>;

/**
 * ELK options added to the C4 defaults for the map: keep the cards' input
 * (previous) order where crossings allow. Node order only: placement edges
 * go to ELK in id order, which carries no meaning to preserve.
 */
export const LEVEL_LAYOUT_OPTIONS: Record<string, string> = {
  "elk.layered.considerModelOrder.strategy": "PREFER_NODES",
};

/** Scope layouts by signature, least recently used dropped first. */
export interface LayoutCache {
  get(key: string): LevelPositions | undefined;
  set(key: string, value: LevelPositions): void;
  clear(): void;
}

export function createLayoutCache(limit = 64): LayoutCache {
  const entries = new Map<string, LevelPositions>();
  return {
    get(key) {
      const hit = entries.get(key);
      if (hit) {
        entries.delete(key);
        entries.set(key, hit);
      }
      return hit;
    },
    set(key, value) {
      entries.delete(key);
      entries.set(key, value);
      if (entries.size > limit) entries.delete(entries.keys().next().value!);
    },
    clear: () => entries.clear(),
  };
}

export type LevelLayoutInput =
  | { kind: "columns"; view: LevelView }
  | { kind: "scopes"; plan: LevelLayoutPlan };

/** What a view needs laid out; pure, so a hook can key on it before any ELK runs. */
export function levelLayoutInput(
  view: LevelView,
  edges: readonly LevelsEdge[],
  sizes: LevelNodeSizes = LEVEL_NODE_SIZES,
): LevelLayoutInput {
  if (view.level === "focus" && view.focus) return { kind: "columns", view };
  return { kind: "scopes", plan: planLevelLayout(view.nodes, edges, sizes) };
}

/** One key per layout: equal keys lay out to equal positions, so a hook can skip ELK on a match. */
export function levelLayoutKey(input: LevelLayoutInput, sizes: LevelNodeSizes = LEVEL_NODE_SIZES): string {
  if (input.kind === "scopes") return planSignature(input.plan);
  const { width, height } = sizes.service;
  return [`columns:${width}x${height}`, ...input.view.nodes.map((n) => n.id)].join("|");
}

export interface LevelLayoutOptions {
  cache?: LayoutCache;
  /** Positions last shown, for input order. */
  previous?: ReadonlyMap<string, { x: number; y: number }> | undefined;
  sizes?: LevelNodeSizes;
  /** Run ELK in a worker when one can start. On by default. */
  worker?: boolean;
}

export async function computeLevelLayout(
  input: LevelLayoutInput,
  options: LevelLayoutOptions = {},
): Promise<LevelPositions> {
  const sizes = options.sizes ?? LEVEL_NODE_SIZES;
  if (input.kind === "columns") return placeFocusColumns(input.view.focus!, { size: sizes.service });

  // The key leaves out input order on purpose: a scope seen before comes back
  // exactly as it was shown, which is the stability the order exists for.
  const run = async (scope: LayoutScope): Promise<LevelPositions> => {
    const key = scopeSignature(scope);
    const hit = options.cache?.get(key);
    if (hit) return hit;
    const positions = await computeC4Layout(orderByPrevious(scope.nodes, options.previous), scope.edges, {
      worker: options.worker ?? true,
      layoutOptions: LEVEL_LAYOUT_OPTIONS,
    });
    options.cache?.set(key, positions);
    return positions;
  };

  const { plan } = input;
  const fitted = new Map<string, FittedContainer>(
    await Promise.all(plan.interiors.map(async (s) => [s.id, fitContainer(await run(s))] as const)),
  );
  // An open container with nothing laid out inside keeps its closed card size.
  const empty = sizes.group;
  const root: LayoutScope = {
    ...plan.root,
    nodes: plan.root.nodes.map((n) => {
      if (n.width > 0) return n;
      const box = fitted.get(n.id) ?? empty;
      return { ...n, width: box.width, height: box.height };
    }),
  };
  const out: LevelPositions = new Map(await run(root));
  for (const box of fitted.values()) for (const [id, p] of box.children) out.set(id, p);
  return out;
}
