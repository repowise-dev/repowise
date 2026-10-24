"use client";

/**
 * Key for the Live System Map, as one caption row between the controls and
 * the canvas: the line styles actually drawn (structural ink, co-change
 * dotted, candidate dashed), the health mark's three bands, and how to get
 * more. Reads the same registries the map renders from, so the two cannot
 * drift, and lists only what the graph contains.
 */

import type { SystemEdgeMatchType } from "@repowise-dev/types";
import { HEALTH_MARK_BANDS } from "../../health/tokens";
import { matchTypeDash } from "./edge-kinds";

function Item({ swatch, label }: { swatch: React.ReactNode; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
      {swatch}
      {label}
    </span>
  );
}

function Line({ dash, color }: { dash: string; color: string }) {
  return (
    <svg width={22} height={6} aria-hidden>
      <line x1={0} y1={3} x2={22} y2={3} stroke={color} strokeWidth={1.75} strokeDasharray={dash} />
    </svg>
  );
}

function Dot({ color }: { color: string }) {
  return <span aria-hidden className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: color }} />;
}

const LINE_LABEL: Record<SystemEdgeMatchType, string> = {
  exact: "Structural, exact match",
  manual: "Structural, declared by hand",
  candidate: "Structural, candidate match",
  inferred: "Co-change from git history",
};

/** "8+", "4 to 8", "below 4": each band's range, read off the shared edges. */
function markRange(i: number): string {
  const b = HEALTH_MARK_BANDS[i]!;
  if (i === 0) return `${b.min}+`;
  const above = HEALTH_MARK_BANDS[i - 1]!.min;
  if (i === HEALTH_MARK_BANDS.length - 1) return `below ${above}`;
  return `${b.min} to ${above}`;
}

export interface SystemMapLegendProps {
  /** Match types present on the drawn edges; only these get a key. */
  matchTypes?: ReadonlySet<SystemEdgeMatchType>;
}

export function SystemMapLegend({ matchTypes }: SystemMapLegendProps) {
  const order: SystemEdgeMatchType[] = ["exact", "manual", "candidate", "inferred"];
  const shown = order.filter((m) => !matchTypes || matchTypes.has(m));
  // Exact and manual draw the same solid line; one key covers both.
  const lines = shown.filter((m) => !(m === "manual" && shown.includes("exact")));

  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5">
      {lines.map((m) => (
        <Item
          key={m}
          label={m === "exact" && shown.includes("manual") ? "Structural, exact or declared" : LINE_LABEL[m]}
          swatch={
            <Line
              dash={matchTypeDash(m)}
              color={m === "inferred" ? "var(--color-text-tertiary)" : "var(--color-diagram-edge)"}
            />
          }
        />
      ))}
      <span className="inline-flex items-center gap-2.5">
        <span className="text-xs text-[var(--color-text-tertiary)]">Repository health</span>
        {HEALTH_MARK_BANDS.map((b, i) => (
          <Item key={b.band} swatch={<Dot color={b.color} />} label={`${b.label} ${markRange(i)}`} />
        ))}
      </span>
      <span className="text-xs text-[var(--color-text-tertiary)]">
        Hover a line for its kind and weight. Click a service or line for details.
      </span>
    </div>
  );
}
