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
  type BlastMap,
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

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}...` : text;
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/**
 * Where a node's label goes: outward along its angle from the centre, so
 * first-ring names spread around the ring instead of stacking on one side.
 * A lone changed file at the very centre has no angle; its label sits below.
 */
function labelPlacement(node: { x: number; y: number; r: number }): {
  x: number;
  y: number;
  anchor: "start" | "middle" | "end";
} {
  if (node.x === CX && node.y === CY) return { x: node.x, y: node.y + node.r + 13, anchor: "middle" };
  const angle = Math.atan2(node.y - CY, node.x - CX);
  const cos = Math.cos(angle);
  const sin = Math.sin(angle);
  const out = node.r + 5;
  const below = sin > 0.7 ? 6 : 0;
  const anchor = Math.abs(cos) < 0.3 ? "middle" : cos < 0 ? "end" : "start";
  return { x: node.x + cos * out, y: node.y + sin * out + 3.5 + below, anchor };
}

/** Changed files carry an ink outline; files with no test evidence a dashed one. */
function fileStroke(node: MapFileNode): { stroke: string; strokeWidth: number; strokeDasharray?: string } {
  const changed = node.hop === 0;
  return {
    stroke: changed ? INK : "var(--color-border-hover)",
    strokeWidth: changed ? 2 : 1.25,
    ...(node.untested ? { strokeDasharray: "2.5 2" } : {}),
  };
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
  const marks = describeMarks(node);
  const role = node.hop === 0 ? "Changed" : "Imports a changed file";
  const place = labelPlacement(node);
  const body = (
    <g className="group/node">
      <circle
        cx={node.x}
        cy={node.y}
        r={node.r}
        fill={fillOf(node)}
        {...fileStroke(node)}
        className="transition-[stroke-width] group-hover/node:[stroke-width:3]"
      />
      {node.isHotspot && <HotspotMark x={node.x} y={node.y} r={node.r} />}
      {showLabel && (
        <text
          x={place.x}
          y={place.y}
          textAnchor={place.anchor}
          className="fill-[var(--color-text-secondary)] group-hover/node:fill-[var(--color-text-primary)]"
          style={{ fontSize: 10.5, fontFamily: "var(--font-mono, monospace)" }}
        >
          {truncate(label, 18)}
        </text>
      )}
      <title>{`${node.path}\n${role} · ${marks.join(" · ")}`}</title>
    </g>
  );
  if (!href) return body;
  return (
    <a
      href={href}
      aria-label={`${node.path}: ${marks.join(", ")}`}
      className="focus:outline-none [&:focus-visible>g>circle]:[stroke-width:3]"
    >
      {body}
    </a>
  );
}

/** A module's marks in words; empty when it holds none. */
function moduleMarks(node: MapModuleNode): string[] {
  const marks: string[] = [];
  if (node.hotspots) marks.push(plural(node.hotspots, "hotspot"));
  if (node.weak) marks.push(`${node.weak} weak health`);
  if (node.untested) marks.push(`${node.untested} untested`);
  return marks;
}

/** The short line under a module's name: files, hotspots, weak health. */
function moduleCountLine(node: MapModuleNode): string {
  const parts = [plural(node.files.length, "file")];
  if (node.hotspots > 0) parts.push(`${node.hotspots} hot`);
  if (node.weak > 0) parts.push(`${node.weak} weak`);
  return parts.join(" · ");
}

function onActivate(handler: () => void) {
  return (e: { key: string; preventDefault: () => void }) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    e.preventDefault();
    handler();
  };
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
  const textX = anchorEnd ? node.x - node.r - 5 : node.x + node.r + 5;
  const anchor = anchorEnd ? "end" : "start";
  const marks = moduleMarks(node);
  const markText = marks.join(", ");
  const name = node.name.split("/").slice(-2).join("/");
  return (
    <g
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      aria-label={[`${node.name}: ${plural(node.files.length, "file")}, ${node.hop} hops out`, ...marks].join(", ")}
      onClick={onSelect}
      onKeyDown={onActivate(onSelect)}
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
        x={textX}
        y={node.y - 1}
        textAnchor={anchor}
        className="fill-[var(--color-text-secondary)]"
        style={{ fontSize: 10.5, fontFamily: "var(--font-mono, monospace)" }}
      >
        {truncate(name, 24)}
      </text>
      <text x={textX} y={node.y + 11} textAnchor={anchor} className="fill-[var(--color-text-tertiary)]" style={{ fontSize: 9.5 }}>
        {moduleCountLine(node)}
      </text>
      <title>{[`${node.name}`, `${plural(node.files.length, "file")}, nearest ${node.hop} hops out`, markText, "Click to list them"].filter(Boolean).join("\n")}</title>
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

function RingGuides({ map }: { map: BlastMap }) {
  const drawn = map.ringRadii.slice(0, Math.max(1, Math.min(3, map.maxHop)));
  const name = ["1 hop", "2 hops", "3+ hops"];
  return (
    <>
      {drawn.map((r, i) => (
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
            {name[i]}
          </text>
        </g>
      ))}
    </>
  );
}

/** Each drawn node to the node it hangs from, or to the hub when that one is not drawn. */
function Edges({ map, nodeByKey }: { map: BlastMap; nodeByKey: Map<string, MapFileNode> }) {
  const from = (key: string | null) => (key ? nodeByKey.get(key) : undefined) ?? { x: map.cx, y: map.cy };
  const line = (key: string, a: { x: number; y: number }, b: { x: number; y: number }, opacity: number) => (
    <line key={key} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={EDGE} strokeOpacity={opacity} strokeWidth={1} />
  );
  return (
    <>
      {map.modules.map((m) => line(`e:${m.key}`, from(m.parentKey), m, m.parentKey ? 0.35 : 0.12))}
      {map.firstHop.map((n) => line(`e:${n.key}`, from(n.parentKey), n, 0.45))}
    </>
  );
}

/** What exists but is not drawn, so the picture never claims to be everything. */
function NotDrawnNote({ map }: { map: BlastMap }) {
  const parts: string[] = [];
  if (map.hiddenChanged > 0) parts.push(`${map.hiddenChanged} more changed files`);
  if (map.hiddenFirstHop > 0) parts.push(`${map.hiddenFirstHop} more files one hop out (the riskiest are drawn)`);
  if (map.hiddenModules > 0) parts.push(`${map.hiddenModules} smaller modules holding ${map.hiddenModuleFiles} files`);
  if (parts.length === 0) return null;
  return (
    <p className="text-[11px] text-[var(--color-text-tertiary)]">
      Not drawn: {parts.join("; ")}. The tables below list every file.
    </p>
  );
}

function FileRef({ path, href }: { path: string; href: string | undefined }) {
  const cls = "min-w-0 break-all font-mono text-[var(--color-text-primary)]";
  return href ? (
    <a href={href} className={`${cls} hover:underline`}>
      {path}
    </a>
  ) : (
    <span className={cls}>{path}</span>
  );
}

/** The files of the module picked on the map. */
function ModuleFiles({ module, fileHref }: { module: MapModuleNode; fileHref: ((path: string) => string) | undefined }) {
  return (
    <div className="border-t border-[var(--color-border-default)] pt-3">
      <p className="mb-2 text-xs text-[var(--color-text-secondary)]">
        <span className="font-mono text-[var(--color-text-primary)]">{module.name}</span>: {module.files.length} files
        reached, nearest {module.hop} hops out
      </p>
      <ul className="max-h-64 space-y-0.5 overflow-y-auto text-xs">
        {module.files.map((f) => (
          <li key={f.path} className="flex flex-wrap items-baseline justify-between gap-x-3">
            <FileRef path={f.path} href={fileHref?.(f.path)} />
            <span className="text-[var(--color-text-tertiary)]">{describeMarks(f).join(" · ")}</span>
          </li>
        ))}
      </ul>
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
  const dot = (n: MapFileNode, showLabel: boolean) => (
    <FileDot key={n.key} node={n} label={labels.get(n.path) ?? n.path} href={fileHref?.(n.path)} showLabel={showLabel} />
  );

  return (
    <div className="space-y-3">
      <div className="overflow-hidden">
        <svg
          viewBox={`0 0 ${map.width} ${map.height}`}
          className="mx-auto h-auto w-full max-w-[820px]"
          role="group"
          aria-label={`Blast radius: ${map.changed.length} changed files at the centre, ${map.firstHop.length} files one hop out, ${map.modules.length} modules further out`}
        >
          <RingGuides map={map} />
          <Edges map={map} nodeByKey={nodeByKey} />
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
          {map.firstHop.map((n) => dot(n, true))}
          {map.changed.map((n) => dot(n, labelChanged))}
          {!labelChanged && (
            <text x={map.cx} y={map.cy + 74} textAnchor="middle" className="fill-[var(--color-text-tertiary)]" style={{ fontSize: 10 }}>
              {map.changed.length + map.hiddenChanged} changed files
            </text>
          )}
        </svg>
      </div>

      <Legend />
      <NotDrawnNote map={map} />
      {selected && <ModuleFiles module={selected} fileHref={fileHref} />}
    </div>
  );
}
