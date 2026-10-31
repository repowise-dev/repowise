/**
 * The canvas's hover card state. The renderer picks the hovered card once per
 * frame (see `pointer-pick.ts`) and reports it through `onHoverRef`. React
 * re-renders only when the hovered item changes; while it stays the same the
 * mounted card just follows the pointer (`placeHoverCard`).
 */

import { type RefObject, useCallback, useRef, useState } from "react";
import type { ExternalLane } from "./externals";
import { placeHoverCard } from "./hover-card";
import type { PickEntry } from "./pick";
import type { ZoomNode } from "./types";
import { type HoverState, setCursor } from "./use-zoom-input";

/** The item the hover card shows: what a new pick is compared against. */
interface HoveredItem {
  id: string | null;
  lane: ExternalLane | undefined;
  byId: ReadonlyMap<string, ZoomNode> | null;
}

/** The hover card's content for a pick: a node, an external card in a rail, or nothing. */
function hoverAt(
  hit: PickEntry | null,
  nodeById: ReadonlyMap<string, ZoomNode>,
  sx: number,
  sy: number,
): HoverState | null {
  if (!hit) return null;
  const node = nodeById.get(hit.id);
  if (node) return { node, sx, sy };
  const card = hit.lane?.cards.find((c) => c.id === hit.id);
  return card && hit.lane ? { card, lane: hit.lane, sx, sy } : null;
}

export function useZoomHover(
  canvasRef: RefObject<HTMLCanvasElement | null>,
  nodeById: ReadonlyMap<string, ZoomNode>,
) {
  const [hover, setHover] = useState<HoverState | null>(null);
  const cardRef = useRef<HTMLDivElement | null>(null);
  const hovered = useRef<HoveredItem>({ id: null, lane: undefined, byId: null });
  const hideHover = useCallback(() => {
    hovered.current.id = null;
    setHover(null);
  }, []);
  const onHoverRef = useRef<(hit: PickEntry | null, sx: number, sy: number) => void>(() => {});
  onHoverRef.current = (hit, sx, sy) => {
    // An external card does not select, but a double-click on it goes somewhere.
    if (canvasRef.current) setCursor(canvasRef.current, hit ? "pointer" : "grab");
    const last = hovered.current;
    const id = hit?.id ?? null;
    if (id === last.id && hit?.lane === last.lane && nodeById === last.byId) {
      if (id !== null) placeHoverCard(cardRef.current, sx, sy, canvasRef.current);
      return;
    }
    last.id = id;
    last.lane = hit?.lane;
    last.byId = nodeById;
    setHover(hoverAt(hit, nodeById, sx, sy));
  };
  return { hover, hideHover, onHoverRef, cardRef };
}
