"use client";

/**
 * The canvas's hover cards: one for a node, one for an external system in a
 * rail. Deliberately light tooltips: the full breakdown lives in the host's
 * detail sheet (open on click), so a hover stays calm instead of dumping
 * numbers. Both flip toward the canvas interior near its right or bottom edge.
 */

import { type ReactNode, type Ref, memo, useMemo } from "react";
import { externalImporters } from "./externals";
import { healthBandLabel, KIND_LABEL, nodeRoles } from "./node-signals";
import { formatVerbCounts, relationDirections, summarizeRelations } from "./relation-summary";
import type { HoverState } from "./use-zoom-input";
import type { ZoomCanvasLabels } from "./zoom-canvas-labels";
import type { ZoomNode, ZoomRelation } from "./types";

/** How many importing children an external's hover card names. */
const TOP_IMPORTERS = 3;

const MICRO = "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";

/** The card's offset from the pointer, flipped toward the interior near an edge. */
function hoverTransform(sx: number, sy: number, canvas: HTMLCanvasElement | null): string {
  const w = canvas?.clientWidth ?? 0;
  const h = canvas?.clientHeight ?? 0;
  const tx = w > 0 && sx > w * 0.66 ? "calc(-100% - 14px)" : "14px";
  const ty = h > 0 && sy > h * 0.66 ? "calc(-100% - 14px)" : "14px";
  return `translate(${tx}, ${ty})`;
}

/**
 * Move a mounted hover card to a new pointer point without re-rendering it:
 * the pointer moves every frame, the card's content only when the hovered item
 * changes.
 */
export function placeHoverCard(
  el: HTMLElement | null,
  sx: number,
  sy: number,
  canvas: HTMLCanvasElement | null,
): void {
  if (!el) return;
  el.style.left = `${sx}px`;
  el.style.top = `${sy}px`;
  el.style.transform = hoverTransform(sx, sy, canvas);
}

/** Placed beside the pointer, flipped toward the interior near an edge (N2). */
function Floating({
  hover,
  canvas,
  cardRef,
  children,
}: {
  hover: HoverState;
  canvas: HTMLCanvasElement | null;
  cardRef: Ref<HTMLDivElement> | undefined;
  children: ReactNode;
}) {
  return (
    <div
      ref={cardRef}
      className="pointer-events-none absolute z-10 max-w-[16rem] rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-3 py-2 text-xs shadow-xl"
      style={{ left: hover.sx, top: hover.sy, transform: hoverTransform(hover.sx, hover.sy, canvas) }}
    >
      {children}
    </div>
  );
}

export const HoverCard = memo(function HoverCard({
  hover,
  canvas,
  cardRef,
  relationsByNode,
  relationVerbs,
  nodeById,
  labels,
}: {
  hover: HoverState;
  canvas: HTMLCanvasElement | null;
  /** The card element, for `placeHoverCard` while the hovered item stays the same. */
  cardRef?: Ref<HTMLDivElement>;
  relationsByNode: Map<string, ZoomRelation[]> | undefined;
  relationVerbs: ReadonlySet<string> | null;
  nodeById: ReadonlyMap<string, ZoomNode>;
  labels: ZoomCanvasLabels;
}) {
  return (
    <Floating hover={hover} canvas={canvas} cardRef={cardRef}>
      {"node" in hover ? (
        <NodeBody
          node={hover.node}
          relations={relationsByNode?.get(hover.node.id)}
          relationVerbs={relationVerbs}
          labels={labels}
        />
      ) : (
        <ExternalBody hover={hover} nodeById={nodeById} labels={labels} />
      )}
    </Floating>
  );
});

/**
 * Kind, name, path, a one-line summary and, when the host supplies the index,
 * what this box's arrows mean. The relation line is here because the arrows
 * appear on hover: the words sit beside the arrows while the reader is
 * looking at them.
 */
function NodeBody({
  node,
  relations,
  relationVerbs,
  labels,
}: {
  node: ZoomNode;
  relations: ZoomRelation[] | undefined;
  relationVerbs: ReadonlySet<string> | null;
  labels: ZoomCanvasLabels;
}) {
  const span = node.container_ids?.length ?? 0;
  return (
    <>
      <div className={MICRO}>
        {KIND_LABEL[node.kind]}
        {span > 1 && ` · ${labels.containerSpan(span)}`}
      </div>
      <div className="mt-0.5 font-semibold text-[var(--color-text-primary)]">{node.name}</div>
      {node.path && node.path !== node.name && (
        <div className="truncate text-[var(--color-text-tertiary)]">{node.path}</div>
      )}
      {node.summary && (
        <div className="mt-1 line-clamp-2 text-[var(--color-text-secondary)]">{node.summary}</div>
      )}
      <NodeSignals node={node} />
      {relations && relations.length > 0 && (
        <HoverRelations
          nodeId={node.id}
          relations={relations}
          relationVerbs={relationVerbs}
          labels={labels}
        />
      )}
    </>
  );
}

/**
 * The two dots on the card, in words. They are drawn in the corners with no
 * key of their own, so the moment the pointer is on the card is the cheapest
 * place to say what they mean.
 */
function NodeSignals({ node }: { node: ZoomNode }) {
  const roles = nodeRoles(node);
  const band = healthBandLabel(node.health_score);
  if (roles.length === 0 && !band) return null;
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[var(--color-text-secondary)]">
      {roles.length > 0 && (
        <span className="text-[var(--color-accent-primary)]">{roles.join(" · ")}</span>
      )}
      {band && (
        <span className="tabular-nums">
          Health {node.health_score!.toFixed(1)} {band}
        </span>
      )}
    </div>
  );
}

/**
 * The arrows in words, by direction: "→ calls 8 · imports 3" for the ones
 * leaving the card, "←" for the ones arriving, and a note when the per-level
 * cap leaves some of them off the map.
 */
function HoverRelations({
  nodeId,
  relations,
  relationVerbs,
  labels,
}: {
  nodeId: string;
  relations: ZoomRelation[];
  relationVerbs: ReadonlySet<string> | null;
  labels: ZoomCanvasLabels;
}) {
  const { outgoing, incoming } = useMemo(
    () => relationDirections(relations, nodeId),
    [relations, nodeId],
  );
  // Measured against what the map draws, so a filtered view does not claim a cap it is under.
  const drawn = useMemo(
    () =>
      summarizeRelations(
        relationVerbs === null ? relations : relations.filter((r) => relationVerbs.has(r.label)),
      ),
    [relations, relationVerbs],
  );
  return (
    <div className="mt-1.5 space-y-0.5 border-t border-[var(--color-border-default)] pt-1.5 tabular-nums text-[var(--color-text-secondary)]">
      <div className="text-[var(--color-text-tertiary)]">{labels.relationCount(relations.length)}</div>
      {outgoing.length > 0 && (
        <div>
          <span aria-hidden className="mr-1 text-[var(--color-text-tertiary)]">→</span>
          {formatVerbCounts(outgoing)}
        </div>
      )}
      {incoming.length > 0 && (
        <div>
          <span aria-hidden className="mr-1 text-[var(--color-text-tertiary)]">←</span>
          {formatVerbCounts(incoming)}
        </div>
      )}
      {drawn.total > drawn.shown && (
        <div className="text-[var(--color-text-tertiary)]">{labels.capOnMap(drawn.shown)}</div>
      )}
    </div>
  );
}

/**
 * An external system in a rail: what it is, how much the parts here import
 * it against the whole repository, the parts that import it most, and where a
 * double-click goes.
 */
function ExternalBody({
  hover,
  nodeById,
  labels,
}: {
  hover: Extract<HoverState, { card: unknown }>;
  nodeById: ReadonlyMap<string, ZoomNode>;
  labels: ZoomCanvasLabels;
}) {
  const { card, lane } = hover;
  const importers = externalImporters(lane, card.id).slice(0, TOP_IMPORTERS);
  const first = importers[0] ? nodeById.get(importers[0].sourceId) : undefined;
  return (
    <>
      <div className={MICRO}>
        {card.kind}
        {card.external.ecosystem && ` · ${card.external.ecosystem}`}
      </div>
      <div className="mt-0.5 font-semibold text-[var(--color-text-primary)]">{card.title}</div>
      <div className="mt-1 tabular-nums text-[var(--color-text-secondary)]">
        {labels.externalUsage(card.edgeCount, card.external.file_count)}
      </div>
      {importers.length > 0 && (
        <div className="mt-1.5 space-y-0.5 border-t border-[var(--color-border-default)] pt-1.5 tabular-nums text-[var(--color-text-secondary)]">
          <div className="text-[var(--color-text-tertiary)]">{labels.externalMostFrom}</div>
          {importers.map((l) => (
            <div key={l.sourceId} className="flex justify-between gap-3">
              <span className="truncate">{nodeById.get(l.sourceId)?.name ?? l.sourceId}</span>
              <span className="text-[var(--color-text-tertiary)]">{l.edgeCount.toLocaleString("en-US")}</span>
            </div>
          ))}
        </div>
      )}
      {first && <div className="mt-1.5 text-[var(--color-text-tertiary)]">{labels.externalDive(first.name)}</div>}
    </>
  );
}
