"use client";

/**
 * A band: every link between two drawn nodes where at least one end is an
 * aggregate (a group, the tray, a "+N more" card, a focus column), as one
 * stroke per direction. Width is the link count on a log scale and the count
 * is printed on the band, because width alone does not name a number. The
 * kind mix is a sentence on hover or selection; kind is never a hue.
 */

import { memo, type CSSProperties } from "react";
import { BaseEdge, EdgeLabelRenderer, getBezierPath, type EdgeProps } from "@xyflow/react";
import type { BandEdgeData } from "./flow-elements";
import { useEdgeActive, useEdgeFocused } from "./system-map-focus";

/** One source for the band's look, read by the legend too. */
export const BAND_STROKE = { min: 2, max: 10, opacity: 0.55 } as const;

/** Stroke width for a band of `links` links: 2 px for one link, 10 px from about 150. */
export function bandWidth(links: number): number {
  return Math.min(BAND_STROKE.max, BAND_STROKE.min + Math.log2(Math.max(1, links)) * 1.6);
}

const LABEL_BASE = {
  position: "absolute",
  pointerEvents: "none",
  borderRadius: 4,
  background: "var(--color-bg-root)",
  fontFamily: "var(--font-mono, ui-monospace, monospace)",
  fontSize: 10,
  lineHeight: "14px",
} as const;

/** The band's stroke: accent when it or a card it touches is hovered or selected, ink when a lens marks it. */
function bandStyle(links: number, active: boolean, lensed: boolean, dimmed: boolean): CSSProperties {
  const stroke = active ? "var(--color-accent-primary)" : lensed ? "var(--color-text-primary)" : "var(--color-diagram-edge)";
  return {
    stroke,
    strokeWidth: bandWidth(links),
    strokeOpacity: active ? 0.9 : BAND_STROKE.opacity,
    strokeLinecap: "butt",
    opacity: dimmed ? 0.12 : 1,
    transition: "opacity 120ms, stroke 120ms, stroke-opacity 120ms",
  };
}

/** The printed count, always; the sentence under it only while the band itself is hovered or selected. */
function BandLabels({ x, y, links, sentence, active, focused }: { x: number; y: number; links: number; sentence: string; active: boolean; focused: boolean }) {
  return (
    <EdgeLabelRenderer>
      <div
        className="nodrag nopan"
        style={{
          ...LABEL_BASE,
          transform: `translate(-50%, -50%) translate(${x}px, ${y}px)`,
          padding: "0 5px",
          border: `1px solid ${active ? "var(--color-accent-primary)" : "var(--color-border-hover)"}`,
          color: "var(--color-text-primary)",
          fontWeight: 500,
          fontVariantNumeric: "tabular-nums",
          zIndex: focused ? 3 : 1,
        }}
      >
        {links.toLocaleString()}
      </div>
      {focused && (
        <div
          role="tooltip"
          style={{
            ...LABEL_BASE,
            transform: `translate(-50%, 12px) translate(${x}px, ${y}px)`,
            maxWidth: 280,
            padding: "6px 8px",
            border: "1px solid var(--color-border-hover)",
            background: "var(--color-bg-overlay)",
            color: "var(--color-text-primary)",
            fontFamily: "var(--font-sans)",
            fontSize: 12,
            lineHeight: "16px",
            zIndex: 4,
          }}
        >
          {sentence}
        </div>
      )}
    </EdgeLabelRenderer>
  );
}

function SystemMapBandInner(props: EdgeProps) {
  const { id, source, target, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, markerEnd, data } = props;
  const { band, sentence, overlay } = data as unknown as BandEdgeData;
  const active = useEdgeActive(id, source, target);
  const focused = useEdgeFocused(id);
  const [path, labelX, labelY] = getBezierPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition });
  const dimmed = Boolean(overlay?.dimmed) && !active;
  const links = band.summary.links;

  return (
    <g className="sm-edge" data-active={active || overlay?.highlighted ? "" : undefined}>
      <BaseEdge
        id={id}
        path={path}
        {...(markerEnd ? { markerEnd } : {})}
        interactionWidth={Math.max(16, bandWidth(links) + 8)}
        style={bandStyle(links, active, Boolean(overlay?.highlighted), dimmed)}
      />
      {!dimmed && <BandLabels x={labelX} y={labelY} links={links} sentence={sentence} active={active} focused={focused} />}
    </g>
  );
}

export const SystemMapBand = memo(SystemMapBandInner);
