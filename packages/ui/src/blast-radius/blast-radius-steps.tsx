"use client";

import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type MutableRefObject, type RefObject } from "react";
import {
  STEP_LAYOUT,
  neighborBlock,
  pathThrough,
  stepWords,
  type StepBand,
  type StepBlock,
  type StepDirection,
  type StepMap,
  type StepPath,
} from "./blast-step-model";
import { formatNumber } from "../lib/format";

// The change in plum, the product's second accent: orange reads as an alert next to red.
const CHANGED = "var(--color-accent-secondary)";
const REACHED = "var(--color-node-neutral)";
const POOR = "var(--color-error)";
const BAND_REACHED = "var(--color-text-tertiary)";
/** What is off the highlighted path fades to this. */
const DIM = 0.25;
const FADE = "transition-opacity duration-150 motion-reduce:transition-none";

/** Characters a label fits in one column at 11.5px; the drawing never scales, so this holds. */
const LABEL_CHARS = 31;

const ARROWS: Record<string, StepDirection> = {
  ArrowUp: "up",
  ArrowDown: "down",
  ArrowLeft: "left",
  ArrowRight: "right",
};

function clip(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, Math.max(1, max - 1))}…` : text;
}

function blockSummary(b: StepBlock): string {
  if (b.less) return "Show fewer folders in this step";
  const where = b.column === 0 ? "changed" : `${stepWords(b.column)} away`;
  const poor = b.poor ? `, ${formatNumber(b.poor)} with poor health` : "";
  const what = `${b.label}, ${where}: ${formatNumber(b.files)} ${b.files === 1 ? "file" : "files"}${poor}`;
  return b.other ? `${what}. Show every folder in this step` : `${what}. Show these files in the list`;
}

function bandPath(b: StepBand): string {
  const mx = (b.x0 + b.x1) / 2;
  return [
    `M${b.x0},${b.y0}`,
    `C${mx},${b.y0} ${mx},${b.y1} ${b.x1},${b.y1}`,
    `L${b.x1},${b.y1 + b.h1}`,
    `C${mx},${b.y1 + b.h1} ${mx},${b.y0 + b.h0} ${b.x0},${b.y0 + b.h0}`,
    "Z",
  ].join(" ");
}

/** What the per-block handlers share: the map, the refs and the hover state. */
interface Interaction {
  map: StepMap;
  hoverKey: string | null;
  setHoverKey: (key: string | null) => void;
  setActiveKey: (key: string) => void;
  pointer: MutableRefObject<string>;
  firstTap: MutableRefObject<boolean>;
  refs: MutableRefObject<Map<string, HTMLElement | SVGGElement>>;
  activate: (b: StepBlock) => void;
  onClear: () => void;
}

/** The block keyboard focus enters on: the last one focused, else the first. */
function tabStop(map: StepMap, activeKey: string | null): string | null {
  if (activeKey && map.blocks.some((b) => b.key === activeKey)) return activeKey;
  return map.blocks[0]?.key ?? null;
}

/** Where focus goes after a step opens (its "Show fewer" control) or folds (its "N more"). */
function focusAfterToggle(b: StepBlock): string | null {
  if (b.other) return `${b.column}:less`;
  if (b.less) return `${b.column}:other`;
  return null;
}

/** Mouse hover and touch: a mouse highlights on hover; a first touch tap is marked to only highlight. */
function pointerHandlers(b: StepBlock, x: Interaction) {
  const notTouch = () => x.pointer.current !== "touch";
  return {
    onPointerDown: (e: { pointerType: string }) => {
      x.pointer.current = e.pointerType;
      x.firstTap.current = e.pointerType === "touch" && x.hoverKey !== b.key && !b.less && !b.other;
    },
    onPointerMove: (e: { pointerType: string }) => {
      x.pointer.current = e.pointerType;
    },
    onMouseEnter: () => notTouch() && x.setHoverKey(b.key),
    onMouseLeave: () => notTouch() && x.setHoverKey(null),
  };
}

/** Focus highlights for the keyboard; a blur ends it unless the mouse is still over the block. */
function focusHandlers(b: StepBlock, x: Interaction) {
  return {
    onFocus: () => {
      x.setActiveKey(b.key);
      if (x.pointer.current !== "touch") x.setHoverKey(b.key);
    },
    onBlur: () => {
      if (x.pointer.current !== "mouse") x.setHoverKey(null);
    },
  };
}

/** A click picks, except a first tap on touch, which shows the path and the tooltip. */
function clickHandler(b: StepBlock, x: Interaction) {
  return () => {
    if (!x.firstTap.current) return x.activate(b);
    x.firstTap.current = false;
    x.setHoverKey(b.key);
  };
}

/** Arrows move between blocks, Enter or Space picks, Escape clears. */
function keyHandler(b: StepBlock, x: Interaction) {
  const keys: Record<string, () => void> = {
    Enter: () => x.activate(b),
    " ": () => x.activate(b),
    Escape: () => {
      x.setHoverKey(null);
      x.onClear();
    },
  };
  return (e: KeyboardEvent) => {
    const dir = ARROWS[e.key];
    const run = dir ? () => moveFocus(b, dir, x) : keys[e.key];
    if (!run) return;
    if (e.key !== "Escape") e.preventDefault();
    run();
  };
}

function moveFocus(b: StepBlock, dir: StepDirection, x: Interaction) {
  const next = neighborBlock(x.map, b.key, dir);
  if (next) x.refs.current.get(next.key)?.focus();
}

/**
 * Hover, focus and touch for blocks and rows: a mouse highlights on hover; on
 * touch the first tap highlights and the second picks. Keyboard focus
 * highlights, arrows move, Enter or Space picks, Escape clears.
 */
function useStepInteraction(map: StepMap, onActivate: (b: StepBlock) => void, onClear: () => void) {
  const [hoverKey, setHoverKey] = useState<string | null>(null);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const pointer = useRef<string>("mouse");
  // Decided on pointer down, before focus moves: a first tap on touch only highlights.
  const firstTap = useRef(false);
  // After a step opens or folds, focus lands on its new control instead of the page.
  const pendingFocus = useRef<string | null>(null);
  const refs = useRef(new Map<string, HTMLElement | SVGGElement>());

  useEffect(() => {
    // A rebuilt map can drop the hovered block (a step folded); its highlight goes with it.
    if (hoverKey && !map.blocks.some((b) => b.key === hoverKey)) setHoverKey(null);
    const target = pendingFocus.current;
    pendingFocus.current = null;
    if (target) refs.current.get(target)?.focus();
  }, [map, hoverKey]);

  const x: Interaction = {
    map,
    hoverKey,
    setHoverKey,
    setActiveKey,
    pointer,
    firstTap,
    refs,
    activate: (b) => {
      pendingFocus.current = focusAfterToggle(b);
      onActivate(b);
    },
    onClear,
  };
  const tabKey = tabStop(map, activeKey);

  const props = (b: StepBlock) => ({
    ref: (el: HTMLElement | SVGGElement | null) => {
      if (el) refs.current.set(b.key, el);
      else refs.current.delete(b.key);
    },
    tabIndex: b.key === tabKey ? 0 : -1,
    ...pointerHandlers(b, x),
    ...focusHandlers(b, x),
    onClick: clickHandler(b, x),
    onKeyDown: keyHandler(b, x),
  });
  return { hoverKey, props };
}

function Block({
  b,
  selected,
  dimmed,
  bind,
}: {
  b: StepBlock;
  selected: boolean;
  dimmed: boolean;
  bind: ReturnType<ReturnType<typeof useStepInteraction>["props"]>;
}) {
  const L = STEP_LAYOUT;
  const labelY = b.y + Math.min(b.height / 2, L.minBlock / 2) + 4;
  const { ref, ...handlers } = bind;
  if (b.less) {
    return (
      <g
        ref={ref}
        role="button"
        aria-label={blockSummary(b)}
        className={`cursor-pointer focus:outline-none ${FADE}`}
        opacity={dimmed ? DIM : 1}
        {...handlers}
      >
        <rect x={b.x - 4} y={b.y - 2} width={L.colPitch - 8} height={b.height + 4} fill="transparent" />
        <text x={b.x} y={labelY} style={{ fontSize: 11.5 }} className="fill-[var(--color-text-tertiary)] underline">
          {b.label}
        </text>
      </g>
    );
  }
  const poorHeight = b.files ? (b.height * b.poor) / b.files : 0;
  const suffix = `${formatNumber(b.files)}${b.poor ? ` · ${formatNumber(b.poor)} poor` : ""}`;
  const name = clip(b.label, LABEL_CHARS - suffix.length - 1);
  return (
    <g
      ref={ref}
      role="button"
      aria-pressed={b.other ? undefined : selected}
      aria-label={blockSummary(b)}
      className={`group cursor-pointer focus:outline-none ${FADE}`}
      opacity={dimmed ? DIM : 1}
      {...handlers}
    >
      {/* The hit area covers the label too, so the whole row is the target. */}
      <rect x={b.x - 4} y={b.y - 2} width={L.colPitch - 8} height={b.height + 4} fill="transparent" />
      <rect
        x={b.x - 3}
        y={b.y - 3}
        width={L.blockWidth + 6}
        height={b.height + 6}
        rx={4}
        fill="none"
        stroke={CHANGED}
        strokeWidth={selected ? 1.5 : 2}
        className={
          selected
            ? "opacity-100 group-focus-visible:[stroke-width:2]"
            : "opacity-0 group-hover:opacity-40 group-focus-visible:opacity-100"
        }
      />
      <rect x={b.x} y={b.y} width={L.blockWidth} height={b.height} rx={2} fill={b.column === 0 ? CHANGED : REACHED} />
      {poorHeight > 0 && (
        <rect x={b.x} y={b.y + b.height - poorHeight} width={L.blockWidth} height={poorHeight} rx={2} fill={POOR} />
      )}
      <text
        x={b.x + L.blockWidth + L.labelGap}
        y={labelY}
        style={{ fontSize: 11.5 }}
        // A halo in the page colour keeps the label readable over the bands behind it.
        stroke="var(--color-bg-root)"
        strokeWidth={3}
        paintOrder="stroke"
        strokeLinejoin="round"
        className={b.other ? "fill-[var(--color-text-tertiary)]" : "fill-[var(--color-text-primary)]"}
        fontWeight={selected ? 600 : 400}
      >
        {name}
        <tspan dx={6} className="fill-[var(--color-text-tertiary)] tabular-nums" fontWeight={400}>
          {suffix}
        </tspan>
      </text>
    </g>
  );
}

/** A block named on hover or focus: its folder, its count at this step, its poor health and three files. */
function Tooltip({ b, map }: { b: StepBlock; map: StepMap }) {
  if (b.less) return null;
  const L = STEP_LAYOUT;
  const left = Math.min(b.x + L.blockWidth + L.labelGap, map.width - 232);
  const top = b.y + Math.max(b.height, L.minBlock) + 6;
  return (
    <div
      role="tooltip"
      className="pointer-events-none absolute z-10 w-56 space-y-1 rounded-md border border-[var(--color-border-hover)] bg-[var(--color-bg-root)] px-2.5 py-2 text-xs"
      style={{ left, top }}
    >
      <p className="font-medium text-[var(--color-text-primary)]">{b.label}</p>
      <p className="tabular-nums text-[var(--color-text-secondary)]">
        {formatNumber(b.files)} {b.files === 1 ? "file" : "files"} {b.column === 0 ? "changed" : `${stepWords(b.column)} away`}
        {b.poor > 0 && <span className="text-[var(--color-error)]"> · {formatNumber(b.poor)} poor health</span>}
      </p>
      {b.top.length > 0 && (
        <ul className="space-y-0.5 font-mono text-[11px] text-[var(--color-text-tertiary)]">
          {b.top.map((name) => (
            <li key={name} className="truncate">
              {name}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Legend() {
  const swatch = "inline-block h-2 w-2 rounded-[2px]";
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--color-text-tertiary)]">
      <span className="flex items-center gap-1.5">
        <span className={swatch} style={{ background: CHANGED }} aria-hidden /> Changed
      </span>
      <span className="flex items-center gap-1.5">
        <span className={swatch} style={{ background: REACHED }} aria-hidden /> Reached
      </span>
      <span className="flex items-center gap-1.5">
        <span className={swatch} style={{ background: POOR }} aria-hidden /> Poor health
      </span>
    </div>
  );
}

function notDrawn(map: StepMap): string {
  const parts: string[] = [];
  if (map.testsNotDrawn)
    parts.push(`${formatNumber(map.testsNotDrawn)} test ${map.testsNotDrawn === 1 ? "file" : "files"} that import the change`);
  if (map.changedTestsNotDrawn)
    parts.push(`${formatNumber(map.changedTestsNotDrawn)} changed test ${map.changedTestsNotDrawn === 1 ? "file" : "files"}`);
  return parts.join(" and ");
}

/** The container's width once measured; `null` before (and where ResizeObserver is missing). */
function useWidth<T extends HTMLElement>(): [RefObject<T | null>, number | null] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState<number | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([entry]) => setWidth(entry?.contentRect.width ?? null));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

interface ViewProps {
  map: StepMap;
  selectedKey: string | null;
  selectedBand: string | null;
  path: StepPath | null;
  hoverBlock: StepBlock | undefined;
  bind: ReturnType<typeof useStepInteraction>["props"];
  onSelectBand: (band: StepBand) => void;
}

/**
 * The same steps stacked top to bottom, for a box too narrow for the
 * drawing: each step a heading, each folder a row with a bar sized by its
 * files and the poor-health share in red. Bands are left out; the highlight
 * dims the rows off the hovered row's path.
 */
function StepStack({ map, selectedKey, path, bind }: ViewProps) {
  const most = Math.max(1, ...map.blocks.map((b) => b.files));
  return (
    <div className="space-y-4">
      {map.columns.map((c, ci) => {
        const blocks = map.blocks.filter((b) => b.column === ci);
        if (blocks.length === 0) return null;
        return (
          <div key={c.title} className="space-y-1">
            <p className="text-xs text-[var(--color-text-secondary)]">
              {c.title} <span className="tabular-nums text-[var(--color-text-tertiary)]">{formatNumber(c.files)}</span>
            </p>
            <ul>
              {blocks.map((b) => {
                const selected = b.key === selectedKey;
                const width = Math.max(4, (b.files / most) * 100);
                const { ref, ...handlers } = bind(b);
                return (
                  <li key={b.key}>
                    <button
                      ref={ref as (el: HTMLButtonElement | null) => void}
                      type="button"
                      aria-pressed={b.other || b.less ? undefined : selected}
                      aria-label={blockSummary(b)}
                      {...handlers}
                      className={`flex min-h-10 w-full min-w-0 items-center gap-3 rounded-md px-2 text-left text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)] ${FADE} ${
                        selected ? "bg-[var(--color-bg-selected)]" : "hover:bg-[var(--color-bg-elevated)]"
                      } ${path && !path.blocks.has(b.key) ? "opacity-25" : ""}`}
                    >
                      {b.less ? (
                        <span className="text-[var(--color-text-tertiary)] underline">{b.label}</span>
                      ) : (
                        <>
                          <span className="flex h-2 w-16 shrink-0 overflow-hidden rounded-[2px]" aria-hidden>
                            <span className="flex h-full" style={{ width: `${width}%` }}>
                              <span className="h-full" style={{ flex: b.files - b.poor, background: ci === 0 ? CHANGED : REACHED }} />
                              <span className="h-full" style={{ flex: b.poor, background: POOR }} />
                            </span>
                          </span>
                          <span
                            className={`min-w-0 flex-1 truncate ${b.other ? "text-[var(--color-text-tertiary)]" : "text-[var(--color-text-primary)]"} ${selected ? "font-medium" : ""}`}
                          >
                            {b.label}
                          </span>
                          <span className="shrink-0 tabular-nums text-[var(--color-text-tertiary)]">
                            {formatNumber(b.files)}
                            {b.poor > 0 && <span className="text-[var(--color-error)]"> · {formatNumber(b.poor)} poor</span>}
                          </span>
                        </>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

function StepDrawing({ map, selectedKey, selectedBand, path, hoverBlock, bind, onSelectBand }: ViewProps) {
  const selected = map.blocks.find((b) => b.key === selectedKey);
  const reached = map.columns.slice(1).reduce((n, c) => n + c.files, 0);
  return (
    <div className="relative">
      <svg
        width={map.width}
        height={map.height}
        viewBox={`0 0 ${map.width} ${map.height}`}
        className="block max-w-full"
        role="group"
        aria-label={`What depends on the change, by step: ${formatNumber(map.columns[0]!.files)} changed source files reach ${formatNumber(reached)} source files. Arrow keys move between folders; Enter shows a folder's files; Escape clears.`}
      >
        {map.columns.map((c) => (
          <text key={c.title} x={c.x} y={14} style={{ fontSize: 12 }} className="fill-[var(--color-text-secondary)]">
            {c.title}
            <tspan dx={6} className="fill-[var(--color-text-tertiary)] tabular-nums">
              {formatNumber(c.files)}
            </tspan>
          </text>
        ))}
        <g>
          {map.bands.map((b) => {
            const onPath = path ? path.bands.has(b.key) : false;
            const lit = b.key === selectedBand || onPath || (!path && selected && (b.from === selected.key || b.to === selected.key));
            return (
              <path
                key={b.key}
                d={bandPath(b)}
                fill={b.fromChanged ? CHANGED : BAND_REACHED}
                fillOpacity={lit ? 0.34 : 0.14}
                opacity={path && !onPath ? DIM : 1}
                onClick={() => onSelectBand(b)}
                className={`cursor-pointer ${FADE}`}
              >
                <title>{`${formatNumber(b.files)} ${b.files === 1 ? "file" : "files"} reached through this link. Show them in the list`}</title>
              </path>
            );
          })}
        </g>
        {map.blocks.map((b) => (
          <Block
            key={b.key}
            b={b}
            selected={b.key === selectedKey}
            dimmed={Boolean(path && !path.blocks.has(b.key))}
            bind={bind(b)}
          />
        ))}
      </svg>
      {hoverBlock && <Tooltip b={hoverBlock} map={map} />}
    </div>
  );
}

/**
 * The blast radius as steps, left to right: changed folders, then the
 * folders one, two and three imports away. A block's height is its file
 * count and its red share the files with poor health; bands show which
 * folder reaches which.
 *
 * Hovering or focusing a block lights its whole path through the bands and
 * names it; picking a block or a band narrows the file list to it; picking
 * "N more folders" opens that step in place. The drawing is one tab stop,
 * with arrow keys between folders. A box narrower than the drawing gets the
 * same steps stacked top to bottom, so the page never scrolls sideways.
 */
export function BlastRadiusSteps({
  map,
  selectedKey,
  selectedBand = null,
  highlightKey = null,
  onSelect,
  onSelectBand = () => {},
  onToggleColumn = () => {},
  onClear = () => {},
}: {
  map: StepMap;
  selectedKey: string | null;
  /** A picked band's key, lit while it filters the list. */
  selectedBand?: string | null;
  /** A block to light from outside, e.g. the list row under the pointer. */
  highlightKey?: string | null;
  onSelect: (block: StepBlock) => void;
  onSelectBand?: (band: StepBand) => void;
  /** Open or fold a step's "N more folders". */
  onToggleColumn?: (column: number) => void;
  /** Escape: drop the selection. */
  onClear?: () => void;
}) {
  const [box, width] = useWidth<HTMLDivElement>();
  const { hoverKey, props } = useStepInteraction(
    map,
    (b) => (b.other || b.less ? onToggleColumn(b.column) : onSelect(b)),
    onClear,
  );
  const emphasis = hoverKey ?? highlightKey;
  const path = useMemo(
    () => (emphasis && map.blocks.some((b) => b.key === emphasis) ? pathThrough(map, emphasis) : null),
    [map, emphasis],
  );
  const hoverBlock = hoverKey ? map.blocks.find((b) => b.key === hoverKey) : undefined;
  const tests = map.testsNotDrawn + map.changedTestsNotDrawn;
  const stacked = width !== null && width < map.width;
  const view: ViewProps = { map, selectedKey, selectedBand, path, hoverBlock, bind: props, onSelectBand };
  return (
    <div ref={box} className="min-w-0 space-y-3">
      <Legend />
      {stacked ? <StepStack {...view} /> : <StepDrawing {...view} />}
      {tests > 0 && (
        <p className="text-xs text-[var(--color-text-tertiary)]">
          Not drawn: {notDrawn(map)}. A test is a test to run, not reach.
        </p>
      )}
    </div>
  );
}
