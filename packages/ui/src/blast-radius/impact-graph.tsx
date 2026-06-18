"use client";

import { useMemo } from "react";
import type { BlastRadiusResponse } from "@repowise-dev/types/blast-radius";

interface ImpactGraphProps {
  result: BlastRadiusResponse;
  /** The files the user proposed changing (graph centre). */
  changedFiles: string[];
}

const WIDTH = 520;
const HEIGHT = 320;
const CX = WIDTH / 2;
const CY = HEIGHT / 2;
const INNER_R = 96;
const OUTER_R = 144;

function basename(path: string): string {
  return path.split("/").pop() || path;
}

/** Health-band ink for a direct-risk node, mirroring the coupling vocabulary. */
function riskInk(risk: number): string {
  if (risk >= 0.66) return "var(--color-error)";
  if (risk >= 0.33) return "var(--color-warning)";
  return "var(--color-success)";
}

interface PlacedNode {
  key: string;
  label: string;
  full: string;
  x: number;
  y: number;
  r: number;
  fill: string;
}

/**
 * A compact impact graph: the changed files at the centre, direct risks on an
 * inner ring (health-banded), and transitive-affected files on an outer ring.
 * Edges are quiet hairlines, reusing the coupling diagram's dot/edge idiom so
 * the blast radius reads as a picture before the detail tables.
 */
export function ImpactGraph({ result, changedFiles }: ImpactGraphProps) {
  const { centre, direct, transitive } = useMemo(() => {
    const directRows = result.direct_risks.slice(0, 10);
    const transitiveRows = result.transitive_affected.slice(0, 14);

    const centreNodes: PlacedNode[] = changedFiles.slice(0, 6).map((path, i, arr) => {
      const angle = arr.length === 1 ? -Math.PI / 2 : (i / arr.length) * 2 * Math.PI - Math.PI / 2;
      const spread = arr.length === 1 ? 0 : 26;
      return {
        key: `c-${path}`,
        label: basename(path),
        full: path,
        x: CX + Math.cos(angle) * spread,
        y: CY + Math.sin(angle) * spread,
        r: 7,
        fill: "var(--color-accent-primary)",
      };
    });

    const directNodes: PlacedNode[] = directRows.map((d, i) => {
      const angle = (i / Math.max(1, directRows.length)) * 2 * Math.PI - Math.PI / 2;
      return {
        key: `d-${d.path}`,
        label: basename(d.path),
        full: d.path,
        x: CX + Math.cos(angle) * INNER_R,
        y: CY + Math.sin(angle) * INNER_R,
        r: 4 + d.centrality * 4,
        fill: riskInk(d.risk_score),
      };
    });

    const transitiveNodes: PlacedNode[] = transitiveRows.map((t, i) => {
      const angle = (i / Math.max(1, transitiveRows.length)) * 2 * Math.PI - Math.PI / 2 + 0.2;
      return {
        key: `t-${t.path}`,
        label: basename(t.path),
        full: t.path,
        x: CX + Math.cos(angle) * OUTER_R,
        y: CY + Math.sin(angle) * OUTER_R,
        r: 3,
        fill: "var(--color-text-tertiary)",
      };
    });

    return { centre: centreNodes, direct: directNodes, transitive: transitiveNodes };
  }, [result, changedFiles]);

  const hasNodes = centre.length > 0 || direct.length > 0 || transitive.length > 0;
  if (!hasNodes) return null;

  return (
    <svg
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      className="mx-auto h-auto w-full max-w-[560px]"
      role="img"
      aria-label="Impact graph: changed files at centre, direct and transitive affected files around them"
    >
      {/* Edges: centre → direct (quiet), direct ring → transitive (quieter) */}
      {direct.map((d) => (
        <line
          key={`e-${d.key}`}
          x1={CX}
          y1={CY}
          x2={d.x}
          y2={d.y}
          stroke="var(--color-text-tertiary)"
          strokeOpacity={0.25}
          strokeWidth={1}
        />
      ))}
      {transitive.map((t) => (
        <line
          key={`e-${t.key}`}
          x1={CX}
          y1={CY}
          x2={t.x}
          y2={t.y}
          stroke="var(--color-text-tertiary)"
          strokeOpacity={0.1}
          strokeWidth={0.75}
        />
      ))}

      {[...transitive, ...direct, ...centre].map((n) => (
        <g key={n.key}>
          <circle
            cx={n.x}
            cy={n.y}
            r={n.r}
            fill={n.fill}
            stroke="var(--color-bg-surface)"
            strokeWidth={1}
          >
            <title>{n.full}</title>
          </circle>
        </g>
      ))}

      {/* Label only the centre + direct nodes to keep the canvas legible. */}
      {[...centre, ...direct].map((n) => (
        <text
          key={`l-${n.key}`}
          x={n.x}
          y={n.y - n.r - 3}
          textAnchor="middle"
          className="fill-[var(--color-text-secondary)]"
          style={{ fontSize: 10 }}
        >
          {n.label.length > 18 ? `${n.label.slice(0, 17)}…` : n.label}
        </text>
      ))}
    </svg>
  );
}
