"use client";

import { useMemo } from "react";
import { disambiguateBasenames } from "../lib/format";
import {
  CX,
  CY,
  buildBlastMap,
  describeMarks,
  needsLook,
  type BlastMap,
  type MapFileNode,
  type MapModuleNode,
  type Reach,
} from "./blast-map-model";

const INK = "var(--color-text-primary)";
const EDGE = "var(--color-text-tertiary)";
const CALM_FILL = "var(--color-node-neutral)";
const CALM_STROKE = "var(--color-text-tertiary)";
const LOOK = "var(--color-warning)";

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}...` : text;
}

function plural(n: number, word: string): string {
  return `${n.toLocaleString()} ${word}${n === 1 ? "" : "s"}`;
}

function hopWords(hop: number): string {
  return hop <= 1 ? "imports the change directly" : `${hop} imports away`;
}

/**
 * Where a node's label goes: outward along its angle from the centre, so
 * names spread around the ring instead of stacking on one side. A lone
 * changed file at the very centre has no angle; its label sits below.
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

function Label({ node, text, strong }: { node: { x: number; y: number; r: number }; text: string; strong?: boolean }) {
  if (!text) return null;
  const place = labelPlacement(node);
  return (
    <text
      x={place.x}
      y={place.y}
      textAnchor={place.anchor}
      stroke="var(--color-bg-root)"
      strokeWidth={3}
      paintOrder="stroke"
      className={strong ? "fill-[var(--color-text-primary)]" : "fill-[var(--color-text-secondary)]"}
      style={{ fontSize: 10.5, fontFamily: "var(--font-mono, monospace)" }}
    >
      {text}
    </text>
  );
}

function FileDot({ node, label, href }: { node: MapFileNode; label: string; href: string | undefined }) {
  const changed = node.hop === 0;
  const look = needsLook(node);
  const role = changed ? "Changed" : hopWords(node.hop);
  const marks = describeMarks(node);
  const body = (
    <g>
      <circle
        cx={node.x}
        cy={node.y}
        r={node.r}
        fill={look ? LOOK : changed ? "var(--color-bg-surface)" : CALM_FILL}
        fillOpacity={look ? 0.45 : 1}
        stroke={changed ? INK : look ? LOOK : CALM_STROKE}
        strokeWidth={changed ? 2 : 1.25}
      />
      <Label node={node} text={truncate(label, 20)} strong={changed} />
      <title>{`${node.path}\n${role}: ${marks.join(", ")}`}</title>
    </g>
  );
  if (!href) return body;
  return (
    <a
      href={href}
      aria-label={`${node.path}: ${role}, ${marks.join(", ")}`}
      className="focus:outline-none [&:focus-visible>g>circle]:[stroke-width:3]"
    >
      {body}
    </a>
  );
}

function onActivate(handler: () => void) {
  return (e: { key: string; preventDefault: () => void }) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    e.preventDefault();
    handler();
  };
}

function ModuleBubble({ node, selected, onSelect }: { node: MapModuleNode; selected: boolean; onSelect: () => void }) {
  const look = node.flagged > 0;
  const name = node.name.split("/").slice(-2).join("/");
  const summary = [
    `${node.name}: ${plural(node.files, "file")}, nearest ${hopWords(node.hop)}`,
    look ? `${node.flagged} change often with no test found` : null,
  ]
    .filter(Boolean)
    .join(", ");
  return (
    <g
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      aria-label={`${summary}. Show its files in the list`}
      onClick={onSelect}
      onKeyDown={onActivate(onSelect)}
      className="cursor-pointer focus:outline-none [&:focus-visible>circle]:[stroke-width:3]"
    >
      <circle
        cx={node.x}
        cy={node.y}
        r={node.r}
        fill={look ? LOOK : CALM_FILL}
        fillOpacity={look ? 0.4 : 0.9}
        stroke={selected ? "var(--color-accent-primary)" : look ? LOOK : CALM_STROKE}
        strokeWidth={selected ? 2.5 : 1.25}
      />
      {(node.labelled || selected) && (
        <Label node={node} text={`${truncate(name, 22)} ${node.files.toLocaleString()}`} strong={selected} />
      )}
      <title>{`${summary}\nClick to show its files below`}</title>
    </g>
  );
}

function Legend({ mode }: { mode: BlastMap["mode"] }) {
  const swatch = "inline-block h-2.5 w-2.5 shrink-0 rounded-full";
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-[var(--color-text-tertiary)]">
      <span className="flex items-center gap-1.5">
        <span className={swatch} style={{ border: `2px solid ${INK}` }} /> Changed
      </span>
      <span className="flex items-center gap-1.5">
        <span className={swatch} style={{ background: CALM_FILL, border: `1px solid ${CALM_STROKE}` }} />
        {mode === "modules" ? "Module, sized by the files in it that depend on the change" : "Depends on the change"}
      </span>
      <span className="flex items-center gap-1.5">
        <span
          className={swatch}
          style={{ background: `color-mix(in srgb, ${LOOK} 45%, transparent)`, border: `1px solid ${LOOK}` }}
        />
        {mode === "modules" ? "Holds files that change often and have no test" : "Changes often and has no test"}
      </span>
    </div>
  );
}

function RingGuides({ map }: { map: BlastMap }) {
  const drawn = map.ringRadii.slice(0, Math.max(1, Math.min(3, map.maxHop)));
  const name = ["direct", "2 steps", "3+ steps"];
  return (
    <>
      {drawn.map((r, i) => (
        <g key={r}>
          <circle cx={map.cx} cy={map.cy} r={r} fill="none" stroke="var(--color-border-hover)" strokeDasharray="2 5" />
          {/* Lower left: modules are laid out from the top, so the top is where they crowd. */}
          <text
            x={map.cx - r * 0.72}
            y={map.cy + r * 0.72}
            textAnchor="end"
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
function Edges({ map }: { map: BlastMap }) {
  const nodes = new Map<string, { x: number; y: number }>(
    [...map.changed, ...map.files, ...map.modules].map((n) => [n.key, n]),
  );
  const from = (key: string | null) => (key ? nodes.get(key) : undefined) ?? { x: map.cx, y: map.cy };
  return (
    <>
      {[...map.files, ...map.modules].map((n) => {
        const a = from(n.parentKey);
        return <line key={`e:${n.key}`} x1={a.x} y1={a.y} x2={n.x} y2={n.y} stroke={EDGE} strokeOpacity={0.35} strokeWidth={1} />;
      })}
    </>
  );
}

/** What exists but is not drawn, so the picture never claims to be everything. */
function NotDrawnNote({ map }: { map: BlastMap }) {
  const parts: string[] = [];
  if (map.hiddenChanged > 0) parts.push(plural(map.hiddenChanged, "more changed file"));
  if (map.hiddenModules > 0)
    parts.push(`${plural(map.hiddenModules, "smaller module")} holding ${plural(map.hiddenModuleFiles, "file")}`);
  if (parts.length === 0) return null;
  return <p className="text-xs text-[var(--color-text-tertiary)]">Not drawn: {parts.join("; ")}. The lists below have every file.</p>;
}

export interface BlastRadiusMapProps {
  reach: Reach;
  /** Where a file node goes; omit to draw nodes that do not open anything. */
  fileHref?: ((path: string) => string) | undefined;
  /** The module picked on the map, if any. */
  selectedModule?: string | null | undefined;
  /** Called with a module's name when it is clicked, `null` when it is clicked again. */
  onSelectModule?: ((name: string | null) => void) | undefined;
}

/**
 * Where the change reaches, drawn: the changed files at the centre and, on
 * the rings around them, what depends on them one, two, and three or more
 * imports away. File by file for a small reach; module by module, sized by
 * file count, past that. Amber marks what changes often with no test. A file
 * opens on click; a module shows its files in the list below.
 */
export function BlastRadiusMap({ reach, fileHref, selectedModule = null, onSelectModule }: BlastRadiusMapProps) {
  const map = useMemo(() => buildBlastMap(reach), [reach]);
  const labels = useMemo(() => disambiguateBasenames([...map.changed, ...map.files].map((n) => n.path)), [map]);
  // A crowded centre is listed above; its names would collide here.
  const labelChanged = map.changed.length <= 3;
  const dot = (n: MapFileNode, label: boolean) => (
    <FileDot key={n.key} node={n} label={label ? (labels.get(n.path) ?? n.path) : ""} href={fileHref?.(n.path)} />
  );
  const around = map.mode === "modules" ? plural(map.modules.length, "module") : plural(map.files.length, "file");
  return (
    <div className="space-y-3">
      <svg
        viewBox={`0 0 ${map.width} ${map.height}`}
        className="mx-auto h-auto w-full max-w-[640px]"
        role="group"
        aria-label={`What depends on the change: ${plural(map.changed.length + map.hiddenChanged, "changed file")} at the centre, ${around} around it`}
      >
        <RingGuides map={map} />
        <Edges map={map} />
        <circle cx={map.cx} cy={map.cy} r={3} fill={EDGE} />
        {map.modules.map((m) => (
          <ModuleBubble
            key={m.key}
            node={m}
            selected={m.name === selectedModule}
            onSelect={() => onSelectModule?.(m.name === selectedModule ? null : m.name)}
          />
        ))}
        {map.files.map((n) => dot(n, true))}
        {map.changed.map((n) => dot(n, labelChanged))}
        {!labelChanged && (
          <text x={map.cx} y={map.cy + 62} textAnchor="middle" className="fill-[var(--color-text-tertiary)]" style={{ fontSize: 10 }}>
            {plural(map.changed.length + map.hiddenChanged, "changed file")}
          </text>
        )}
      </svg>
      <Legend mode={map.mode} />
      <NotDrawnNote map={map} />
    </div>
  );
}
