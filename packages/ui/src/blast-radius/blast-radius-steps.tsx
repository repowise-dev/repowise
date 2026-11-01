"use client";

import { useEffect, useRef, useState, type RefObject } from "react";
import { STEP_LAYOUT, stepWords, type StepBand, type StepBlock, type StepMap } from "./blast-step-model";

const CHANGED = "var(--color-accent-primary)";
const REACHED = "var(--color-node-neutral)";
const POOR = "var(--color-error)";
const BAND_REACHED = "var(--color-text-tertiary)";

/** Characters a label fits in one column at 11.5px; the drawing never scales, so this holds. */
const LABEL_CHARS = 31;

function clip(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, Math.max(1, max - 1))}…` : text;
}

function blockSummary(b: StepBlock): string {
  const where = b.column === 0 ? "changed" : `${stepWords(b.column)} away`;
  const poor = b.poor ? `, ${b.poor.toLocaleString()} with poor health` : "";
  return `${b.label}, ${where}: ${b.files.toLocaleString()} ${b.files === 1 ? "file" : "files"}${poor}`;
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

function Block({ b, selected, onSelect }: { b: StepBlock; selected: boolean; onSelect: () => void }) {
  const L = STEP_LAYOUT;
  const poorHeight = b.files ? (b.height * b.poor) / b.files : 0;
  const suffix = `${b.files.toLocaleString()}${b.poor ? ` · ${b.poor.toLocaleString()} poor` : ""}`;
  const name = clip(b.label, LABEL_CHARS - suffix.length - 1);
  const labelY = b.y + Math.min(b.height / 2, L.minBlock / 2) + 4;
  return (
    <g
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      aria-label={`${blockSummary(b)}. Show these files in the list`}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key !== "Enter" && e.key !== " ") return;
        e.preventDefault();
        onSelect();
      }}
      className="group cursor-pointer focus:outline-none"
    >
      <title>{blockSummary(b)}</title>
      {/* The hit area covers the label too, so the whole row is the target. */}
      <rect x={b.x - 4} y={b.y - 2} width={L.colPitch - 8} height={b.height + 4} fill="transparent" />
      <rect
        x={b.x - 3}
        y={b.y - 3}
        width={L.blockWidth + 6}
        height={b.height + 6}
        rx={4}
        fill="none"
        stroke="var(--color-text-primary)"
        strokeWidth={1.25}
        className={selected ? "opacity-100" : "opacity-0 group-hover:opacity-40 group-focus-visible:opacity-100"}
      />
      <rect x={b.x} y={b.y} width={L.blockWidth} height={b.height} rx={2} fill={b.column === 0 ? CHANGED : REACHED} />
      {poorHeight > 0 && (
        <rect x={b.x} y={b.y + b.height - poorHeight} width={L.blockWidth} height={poorHeight} rx={2} fill={POOR} />
      )}
      <text
        x={b.x + L.blockWidth + L.labelGap}
        y={labelY}
        style={{ fontSize: 11.5 }}
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

/**
 * The same steps stacked top to bottom, for a box too narrow for the
 * drawing: each step a heading, each folder a row with a bar sized by its
 * files and the poor-health share in red. Bands are left out; the order
 * still reads changed, then 1, 2 and 3 steps away.
 */
function StepStack({ map, selectedKey, onSelect }: { map: StepMap; selectedKey: string | null; onSelect: (b: StepBlock) => void }) {
  const most = Math.max(1, ...map.blocks.map((b) => b.files));
  return (
    <div className="space-y-4">
      {map.columns.map((c, ci) => {
        const blocks = map.blocks.filter((b) => b.column === ci);
        if (blocks.length === 0) return null;
        return (
          <div key={c.title} className="space-y-1">
            <p className="text-xs text-[var(--color-text-secondary)]">
              {c.title} <span className="tabular-nums text-[var(--color-text-tertiary)]">{c.files.toLocaleString()}</span>
            </p>
            <ul>
              {blocks.map((b) => {
                const selected = b.key === selectedKey;
                const width = Math.max(4, (b.files / most) * 100);
                return (
                  <li key={b.key}>
                    <button
                      type="button"
                      aria-pressed={selected}
                      aria-label={`${blockSummary(b)}. Show these files in the list`}
                      onClick={() => onSelect(b)}
                      className={`flex min-h-10 w-full min-w-0 items-center gap-3 rounded-md px-2 text-left text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)] ${
                        selected ? "bg-[var(--color-bg-selected)]" : "hover:bg-[var(--color-bg-elevated)]"
                      }`}
                    >
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
                        {b.files.toLocaleString()}
                        {b.poor > 0 && <span className="text-[var(--color-error)]"> · {b.poor.toLocaleString()} poor</span>}
                      </span>
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

/**
 * The blast radius as steps, left to right: changed folders, then the
 * folders one, two and three imports away. A block's height is its file
 * count and its red share the files with poor health; bands show which
 * folder reaches which. Picking a block narrows the file list to it.
 *
 * Drawn at a fixed size so labels stay legible. A box narrower than the
 * drawing gets the same steps stacked top to bottom instead, so the page
 * never scrolls sideways.
 */
export function BlastRadiusSteps({
  map,
  selectedKey,
  onSelect,
}: {
  map: StepMap;
  selectedKey: string | null;
  onSelect: (block: StepBlock) => void;
}) {
  const [box, width] = useWidth<HTMLDivElement>();
  const selected = map.blocks.find((b) => b.key === selectedKey);
  const reached = map.columns.slice(1).reduce((n, c) => n + c.files, 0);
  const tests = map.testsNotDrawn + map.changedTestsNotDrawn;
  const stacked = width !== null && width < map.width;
  return (
    <div ref={box} className="min-w-0 space-y-3">
      <Legend />
      {stacked ? (
        <StepStack map={map} selectedKey={selectedKey} onSelect={onSelect} />
      ) : (
        <svg
          width={map.width}
          height={map.height}
          viewBox={`0 0 ${map.width} ${map.height}`}
          className="block max-w-full"
          role="group"
          aria-label={`What depends on the change, by step: ${map.columns[0]!.files.toLocaleString()} changed source files reach ${reached.toLocaleString()} source files`}
        >
          {map.columns.map((c) => (
            <text key={c.title} x={c.x} y={14} style={{ fontSize: 12 }} className="fill-[var(--color-text-secondary)]">
              {c.title}
              <tspan dx={6} className="fill-[var(--color-text-tertiary)] tabular-nums">
                {c.files.toLocaleString()}
              </tspan>
            </text>
          ))}
          <g aria-hidden>
            {map.bands.map((b) => {
              const lit = selected && (b.from === selected.key || b.to === selected.key);
              return (
                <path
                  key={b.key}
                  d={bandPath(b)}
                  fill={b.fromChanged ? CHANGED : BAND_REACHED}
                  fillOpacity={lit ? 0.32 : selected ? 0.08 : 0.14}
                />
              );
            })}
          </g>
          {map.blocks.map((b) => (
            <Block key={b.key} b={b} selected={b.key === selectedKey} onSelect={() => onSelect(b)} />
          ))}
        </svg>
      )}
      {tests > 0 && (
        <p className="text-xs text-[var(--color-text-tertiary)]">
          {tests.toLocaleString()} test {tests === 1 ? "file is" : "files are"} not drawn: a test that imports the change is
          a test to run, not reach.
        </p>
      )}
    </div>
  );
}
