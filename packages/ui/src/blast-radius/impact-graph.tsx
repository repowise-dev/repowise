"use client";

import { useMemo } from "react";
import type { BlastRadiusResponse } from "@repowise-dev/types/blast-radius";

interface ImpactGraphProps {
  result: BlastRadiusResponse;
  /** The files the user proposed changing (graph centre). */
  changedFiles: string[];
}

const WIDTH = 640;
const HEIGHT = 440;
const CX = WIDTH / 2;
const CY = HEIGHT / 2;
const INNER_R = 132;
const OUTER_R = 196;
const MAX_DIRECT = 12;
const MAX_TRANSITIVE = 18;

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
  glow?: string;
  opacity?: number;
}

/**
 * The impact map: changed files at the hub, direct risks on an inner ring
 * (sized by centrality, health-banded, high-risk nodes haloed), and transitive
 * dependents on an outer ring graded by reach depth. Curved hairline edges and
 * faint ring guides give the blast radius a readable shape before the tables.
 */
export function ImpactGraph({ result, changedFiles }: ImpactGraphProps) {
  const { centre, direct, transitive, maxDepth, truncDirect, truncTrans } = useMemo(() => {
    const directRows = result.direct_risks.slice(0, MAX_DIRECT);
    const transitiveRows = result.transitive_affected.slice(0, MAX_TRANSITIVE);
    const depthCeil = Math.max(
      1,
      ...result.transitive_affected.map((t) => t.depth || 1),
    );

    const centreNodes: PlacedNode[] = changedFiles.slice(0, 6).map((path, i, arr) => {
      const angle =
        arr.length === 1 ? -Math.PI / 2 : (i / arr.length) * 2 * Math.PI - Math.PI / 2;
      const spread = arr.length === 1 ? 0 : 30;
      return {
        key: `c-${path}`,
        label: basename(path),
        full: path,
        x: CX + Math.cos(angle) * spread,
        y: CY + Math.sin(angle) * spread,
        r: 8,
        fill: "var(--color-accent-primary)",
        glow: "var(--color-accent-primary)",
      };
    });

    const directNodes: PlacedNode[] = directRows.map((d, i) => {
      const angle = (i / Math.max(1, directRows.length)) * 2 * Math.PI - Math.PI / 2;
      const node: PlacedNode = {
        key: `d-${d.path}`,
        label: basename(d.path),
        full: `${d.path} · risk ${(d.risk_score * 10).toFixed(1)} · centrality ${(d.centrality * 100).toFixed(0)}%`,
        x: CX + Math.cos(angle) * INNER_R,
        y: CY + Math.sin(angle) * INNER_R,
        r: 5 + d.centrality * 6,
        fill: riskInk(d.risk_score),
      };
      if (d.risk_score >= 0.66) node.glow = "var(--color-error)";
      return node;
    });

    const transitiveNodes: PlacedNode[] = transitiveRows.map((t, i) => {
      const angle = (i / Math.max(1, transitiveRows.length)) * 2 * Math.PI - Math.PI / 2 + 0.18;
      // Closer dependents read warmer/stronger; distant ones fade out.
      const depthFrac = (t.depth || 1) / depthCeil;
      return {
        key: `t-${t.path}`,
        label: basename(t.path),
        full: `${t.path} · depth ${t.depth}`,
        x: CX + Math.cos(angle) * OUTER_R,
        y: CY + Math.sin(angle) * OUTER_R,
        r: 3.5,
        fill: "var(--color-text-secondary)",
        opacity: 0.85 - depthFrac * 0.5,
      };
    });

    return {
      centre: centreNodes,
      direct: directNodes,
      transitive: transitiveNodes,
      maxDepth: depthCeil,
      truncDirect: result.direct_risks.length - directRows.length,
      truncTrans: result.transitive_affected.length - transitiveRows.length,
    };
  }, [result, changedFiles]);

  const hasNodes = centre.length > 0 || direct.length > 0 || transitive.length > 0;
  if (!hasNodes) return null;

  return (
    <div>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="mx-auto h-auto w-full max-w-[680px]"
        role="img"
        aria-label="Impact graph: changed files at centre, direct and transitive affected files around them"
      >
        {/* Faint concentric guides for the two rings. */}
        <circle
          cx={CX}
          cy={CY}
          r={INNER_R}
          fill="none"
          stroke="var(--color-border-default)"
          strokeOpacity={0.4}
          strokeDasharray="2 5"
        />
        <circle
          cx={CX}
          cy={CY}
          r={OUTER_R}
          fill="none"
          stroke="var(--color-border-default)"
          strokeOpacity={0.25}
          strokeDasharray="2 5"
        />

        {/* Edges: hub → direct (curved hairlines), hub → transitive (quieter). */}
        {direct.map((d) => (
          <path
            key={`e-${d.key}`}
            d={`M ${CX} ${CY} Q ${(CX + d.x) / 2} ${(CY + d.y) / 2 - 14} ${d.x} ${d.y}`}
            fill="none"
            stroke={d.fill}
            strokeOpacity={0.28}
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
            strokeOpacity={0.08}
            strokeWidth={0.75}
          />
        ))}

        {[...transitive, ...direct, ...centre].map((n) => (
          <g key={n.key} opacity={n.opacity ?? 1}>
            {n.glow && (
              <circle cx={n.x} cy={n.y} r={n.r + 4} fill={n.glow} fillOpacity={0.18} />
            )}
            <circle
              cx={n.x}
              cy={n.y}
              r={n.r}
              fill={n.fill}
              stroke="var(--color-bg-surface)"
              strokeWidth={1.5}
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
            y={n.y - n.r - 4}
            textAnchor="middle"
            className="fill-[var(--color-text-secondary)]"
            style={{ fontSize: 10 }}
          >
            {n.label.length > 20 ? `${n.label.slice(0, 19)}…` : n.label}
          </text>
        ))}
      </svg>

      {/* Legend + truncation notes. */}
      <div className="mt-2 flex flex-wrap items-center justify-center gap-x-4 gap-y-1.5 text-[11px] text-[var(--color-text-tertiary)]">
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-[var(--color-accent-primary)]" />
          Changed
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-[var(--color-error)]" />
          Direct, high risk
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-[var(--color-success)]" />
          Direct, lower risk
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-[var(--color-text-secondary)]" />
          Transitive {maxDepth > 1 ? `(to depth ${maxDepth})` : ""}
        </span>
        <span className="text-[var(--color-text-tertiary)]">· node size = centrality</span>
      </div>
      {(truncDirect > 0 || truncTrans > 0) && (
        <p className="mt-1 text-center text-[10px] text-[var(--color-text-tertiary)]">
          {truncDirect > 0 && `+${truncDirect} more direct`}
          {truncDirect > 0 && truncTrans > 0 && " · "}
          {truncTrans > 0 && `+${truncTrans} more transitive`} not drawn (see tables below)
        </p>
      )}
    </div>
  );
}
