"use client";

import { useMemo, useState } from "react";
import type { AffectedSignal, BlastRadiusResponse } from "@repowise-dev/types/blast-radius";
import { HEALTH_BAND_LABEL, bandForScore, formatScore } from "@repowise-dev/types/health";
import { healthRampFill } from "../health/tokens";
import { disambiguateBasenames } from "../lib/format";
import {
  CX,
  CY,
  buildBlastMap,
  type FileMarks,
  type MapFileNode,
  type MapModuleNode,
} from "./blast-map-model";

const NEUTRAL = "var(--color-node-neutral)";
const INK = "var(--color-text-primary)";
const EDGE = "var(--color-text-tertiary)";

/** The node's fill: its health band on the canonical ramp, neutral when unscored. */
function fillOf(m: FileMarks): string {
  return m.healthScore === null ? NEUTRAL : healthRampFill(m.healthScore);
}

/** Every mark a file carries, in words, for the tooltip and the inspector. */
export function describeMarks(m: FileMarks): string[] {
  const words: string[] = [];
  if (m.healthScore !== null)
    words.push(`${HEALTH_BAND_LABEL[bandForScore(m.healthScore)]} ${formatScore(m.healthScore)}`);
  else words.push("Health not scored");
  if (m.isHotspot) words.push("Hotspot");
  if (m.untested) words.push("No test evidence");
  return words;
}

/** Hotspot mark: a small solid triangle above the node, in ink, so it never reads as a health colour. */
function HotspotMark({ x, y, r }: { x: number; y: number; r: number }) {
  const top = y - r - 9;
  return <path d={`M ${x} ${top} l 4 6 l -8 0 z`} fill={INK} aria-hidden />;
}

function FileDot({
  node,
  label,
  href,
  showLabel,
}: {
  node: MapFileNode;
  label: string;
  href: string | undefined;
  showLabel: boolean;
}) {
  const title = `${node.path}\n${node.hop === 0 ? "Changed" : "Imports a changed file"} · ${describeMarks(node).join(" · ")}`;
  // Labels sit outward along the node's angle from the centre, so first-ring
  // names spread around the ring instead of stacking on one side.
  const angle = Math.atan2(node.y - CY, node.x - CX);
  const out = node.r + 5;
  const lx = node.x + Math.cos(angle) * out;
  const ly = node.y + Math.sin(angle) * out + 3.5;
  const anchor = Math.abs(Math.cos(angle)) < 0.3 ? "middle" : Math.cos(angle) < 0 ? "end" : "start";
  const body = (
    <g className="group/node">
      <circle
        cx={node.x}
        cy={node.y}
        r={node.r}
        fill={fillOf(node)}
        stroke={node.hop === 0 ? INK : "var(--color-border-hover)"}
        strokeWidth={node.hop === 0 ? 2 : 1.25}
        strokeDasharray={node.untested ? "2.5 2" : undefined}
        className="transition-[stroke-width] group-hover/node:[stroke-width:3]"
      />
      {node.isHotspot && <HotspotMark x={node.x} y={node.y} r={node.r} />}
      {showLabel && (
        <text
          x={node.hop === 0 && node.x === CX && node.y === CY ? node.x : lx}
          y={node.hop === 0 && node.x === CX && node.y === CY ? node.y + node.r + 13 : ly + (Math.sin(angle) > 0.7 ? 6 : 0)}
          textAnchor={node.hop === 0 && node.x === CX && node.y === CY ? "middle" : anchor}
          className="fill-[var(--color-text-secondary)] group-hover/node:fill-[var(--color-text-primary)]"
          style={{ fontSize: 10.5, fontFamily: "var(--font-mono, monospace)" }}
        >
          {label.length > 18 ? `${label.slice(0, 17)}...` : label}
        </text>
      )}
      <title>{title}</title>
    </g>
  );
  return href ? (
    <a href={href} aria-label={`${node.path}: ${describeMarks(node).join(", ")}`} className="focus:outline-none [&:focus-visible>g>circle]:[stroke-width:3]">
      {body}
    </a>
  ) : (
    body
  );
}

function ModuleBubble({
  node,
  selected,
  onSelect,
}: {
  node: MapModuleNode;
  selected: boolean;
  onSelect: () => void;
}) {
  const anchorEnd = node.x < CX - 4;
  const marks = [
    node.hotspots ? `${node.hotspots} hotspot${node.hotspots === 1 ? "" : "s"}` : null,
    node.weak ? `${node.weak} weak health` : null,
    node.untested ? `${node.untested} untested` : null,
  ].filter(Boolean);
  const name = node.name.split("/").slice(-2).join("/");
  return (
    <g
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      aria-label={`${node.name}: ${node.files.length} files, ${node.hop} hops out${marks.length ? `, ${marks.join(", ")}` : ""}`}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
      className="cursor-pointer focus:outline-none [&:focus-visible>circle]:[stroke-width:2.5]"
    >
      <circle
        cx={node.x}
        cy={node.y}
        r={node.r}
        fill="var(--color-bg-elevated)"
        stroke={selected ? "var(--color-accent-primary)" : "var(--color-border-hover)"}
        strokeWidth={selected ? 2 : 1.25}
      />
      <text
        x={node.x + (anchorEnd ? -node.r - 5 : node.r + 5)}
        y={node.y - 1}
        textAnchor={anchorEnd ? "end" : "start"}
        className="fill-[var(--color-text-secondary)]"
        style={{ fontSize: 10.5, fontFamily: "var(--font-mono, monospace)" }}
      >
        {name.length > 24 ? `${name.slice(0, 23)}...` : name}
      </text>
      <text
        x={node.x + (anchorEnd ? -node.r - 5 : node.r + 5)}
        y={node.y + 11}
        textAnchor={anchorEnd ? "end" : "start"}
        className="fill-[var(--color-text-tertiary)]"
        style={{ fontSize: 9.5 }}
      >
        {node.files.length} file{node.files.length === 1 ? "" : "s"}
        {node.hotspots > 0 ? ` · ${node.hotspots} hot` : ""}
        {node.weak > 0 ? ` · ${node.weak} weak` : ""}
      </text>
      <title>{`${node.name}\n${node.files.length} files, nearest ${node.hop} hops out${marks.length ? `\n${marks.join(", ")}` : ""}\nClick to list them`}</title>
    </g>
  );
}

function Legend() {
  const swatch = "inline-block h-2.5 w-2.5 rounded-full";
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] text-[var(--color-text-tertiary)]">
      <span className="flex items-center gap-1.5">
        <span className={swatch} style={{ border: `2px solid ${INK}` }} /> Changed
      </span>
      <span className="flex items-center gap-1.5">
        <span className={swatch} style={{ background: healthRampFill(9) }} />
        <span className={swatch} style={{ background: healthRampFill(6) }} />
        <span className={swatch} style={{ background: healthRampFill(3) }} /> Health band
      </span>
      <span className="flex items-center gap-1.5">
        <span className={swatch} style={{ background: NEUTRAL }} /> Not scored
      </span>
      <span className="flex items-center gap-1.5">
        <svg width="10" height="8" aria-hidden>
          <path d="M5 0 l4 7 l-8 0 z" fill={INK} />
        </svg>
        Hotspot
      </span>
      <span className="flex items-center gap-1.5">
        <span className={swatch} style={{ border: "1.5px dashed var(--color-text-tertiary)" }} /> No test evidence
      </span>
      <span className="flex items-center gap-1.5">
        <span className={`${swatch} border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]`} /> Module, sized by files reached
      </span>
    </div>
  );
}

export interface BlastRadiusMapProps {
  result: BlastRadiusResponse & { affected_signals?: AffectedSignal[] };
  changedFiles: string[];
  /** Where a file node goes; omit to draw nodes that do not open anything. */
  fileHref?: ((path: string) => string) | undefined;
}

/**
 * The blast radius drawn: changed files at the centre, the files that import
 * them on the first ring, and modules reached further out on the rings
 * beyond. Fill is health on the canonical band, a triangle marks a hotspot, a
 * dashed outline marks a file with no test evidence. A file opens on click; a
 * module lists its files below the picture.
 */
export function BlastRadiusMap({ result, changedFiles, fileHref }: BlastRadiusMapProps) {
  const map = useMemo(() => buildBlastMap(result, changedFiles), [result, changedFiles]);
  const [selectedModule, setSelectedModule] = useState<string | null>(null);
  const labels = useMemo(
    () => disambiguateBasenames([...map.changed, ...map.firstHop].map((n) => n.path)),
    [map],
  );
  const nodeByKey = useMemo(
    () => new Map([...map.changed, ...map.firstHop].map((n) => [n.key, n] as const)),
    [map],
  );
  const selected = map.modules.find((m) => m.key === selectedModule) ?? null;
  // A crowded centre is listed in the verdict above; its names would collide here.
  const labelChanged = map.changed.length <= 3;

  const edge = (from: { x: number; y: number } | undefined, to: { x: number; y: number }, key: string, opacity: number) => {
    const a = from ?? { x: map.cx, y: map.cy };
    return (
      <line key={key} x1={a.x} y1={a.y} x2={to.x} y2={to.y} stroke={EDGE} strokeOpacity={opacity} strokeWidth={1} />
    );
  };

  return (
    <div className="space-y-3">
      <div className="overflow-hidden">
        <svg
          viewBox={`0 0 ${map.width} ${map.height}`}
          className="mx-auto h-auto w-full max-w-[820px]"
          role="group"
          aria-label={`Blast radius: ${map.changed.length} changed files at the centre, ${map.firstHop.length} files one hop out, ${map.modules.length} modules further out`}
        >
          {map.ringRadii.slice(0, Math.max(1, Math.min(3, map.maxHop))).map((r, i) => (
            <g key={r}>
              <circle cx={map.cx} cy={map.cy} r={r} fill="none" stroke="var(--color-border-default)" strokeOpacity={0.6} strokeDasharray="2 5" />
              <text
                x={map.cx}
                y={map.cy - r + 3}
                textAnchor="middle"
                stroke="var(--color-bg-root)"
                strokeWidth={3}
                paintOrder="stroke"
                className="fill-[var(--color-text-tertiary)]"
                style={{ fontSize: 9.5 }}
              >
                {i === 2 ? "3+ hops" : `${i + 1} hop${i === 0 ? "" : "s"}`}
              </text>
            </g>
          ))}

          {map.modules.map((m) =>
            edge(m.parentKey ? nodeByKey.get(m.parentKey) : undefined, m, `e:${m.key}`, m.parentKey ? 0.35 : 0.12),
          )}
          {map.firstHop.map((n) =>
            edge(n.parentKey ? nodeByKey.get(n.parentKey) : undefined, n, `e:${n.key}`, 0.45),
          )}

          {/* The hub: edges from files whose changed file is not drawn meet here. */}
          <circle cx={map.cx} cy={map.cy} r={3} fill={EDGE} />
          {map.modules.map((m) => (
            <ModuleBubble
              key={m.key}
              node={m}
              selected={m.key === selectedModule}
              onSelect={() => setSelectedModule((cur) => (cur === m.key ? null : m.key))}
            />
          ))}
          {map.firstHop.map((n) => (
            <FileDot key={n.key} node={n} label={labels.get(n.path) ?? n.path} href={fileHref?.(n.path)} showLabel />
          ))}
          {map.changed.map((n) => (
            <FileDot key={n.key} node={n} label={labels.get(n.path) ?? n.path} href={fileHref?.(n.path)} showLabel={labelChanged} />
          ))}
          {!labelChanged && (
            <text x={map.cx} y={map.cy + 74} textAnchor="middle" className="fill-[var(--color-text-tertiary)]" style={{ fontSize: 10 }}>
              {map.changed.length + map.hiddenChanged} changed files
            </text>
          )}
        </svg>
      </div>

      <Legend />

      {(map.hiddenChanged > 0 || map.hiddenFirstHop > 0 || map.hiddenModules > 0) && (
        <p className="text-[11px] text-[var(--color-text-tertiary)]">
          Not drawn:{" "}
          {[
            map.hiddenChanged > 0 && `${map.hiddenChanged} more changed files`,
            map.hiddenFirstHop > 0 && `${map.hiddenFirstHop} more files one hop out (the riskiest are drawn)`,
            map.hiddenModules > 0 &&
              `${map.hiddenModules} smaller modules holding ${map.hiddenModuleFiles} files`,
          ]
            .filter(Boolean)
            .join("; ")}
          . The tables below list every file.
        </p>
      )}

      {selected && (
        <div className="border-t border-[var(--color-border-default)] pt-3">
          <p className="mb-2 text-xs text-[var(--color-text-secondary)]">
            <span className="font-mono text-[var(--color-text-primary)]">{selected.name}</span>: {selected.files.length} files reached,
            nearest {selected.hop} hops out
          </p>
          <ul className="max-h-64 space-y-0.5 overflow-y-auto text-xs">
            {selected.files.map((f) => {
              const href = fileHref?.(f.path);
              return (
                <li key={f.path} className="flex flex-wrap items-baseline justify-between gap-x-3">
                  {href ? (
                    <a href={href} className="min-w-0 break-all font-mono text-[var(--color-text-primary)] hover:underline">
                      {f.path}
                    </a>
                  ) : (
                    <span className="min-w-0 break-all font-mono text-[var(--color-text-primary)]">{f.path}</span>
                  )}
                  <span className="text-[var(--color-text-tertiary)]">{describeMarks(f).join(" · ")}</span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
