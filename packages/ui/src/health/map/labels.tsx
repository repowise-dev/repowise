"use client";

/**
 * Area names: real-px HTML over the canvas, crisp at any zoom.
 *
 * React renders one button per laid-out area; the frame loop places them with
 * {@link placeLabels}, writing styles directly, so a pan moves the names
 * without a React render. A solid disc carries its name and figure inside it;
 * an open one carries its name just above its ring. A name is also a way into
 * its area.
 */

import type { MutableRefObject } from "react";
import type { DiscView, FieldView } from "./area-view";

/** A solid disc names itself once its radius clears this (px). */
const INSIDE_MIN_R = 26;
/** An open ring names itself once its radius clears this (px). */
const ABOVE_MIN_R = 40;
/** Rough label box, for collision culling. */
const LABEL_H = 18;
const LABEL_GAP = 6;

export interface AreaLabel {
  path: string;
  name: string;
  /** The lens's figure for the area, if any. */
  text: string | null;
  /** One sub-folder fills this folder: while open, its label speaks for both. */
  dominated?: boolean;
}

interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Label sizes are measured once and cached, so a frame rarely forces layout. */
const sizes = new WeakMap<HTMLElement, { w: number; h: number }>();
function sizeOf(el: HTMLElement): { w: number; h: number } {
  // Keyed on the text and the state: an open folder lays its label out in a row.
  const key = `${el.textContent ?? ""}|${el.dataset.state ?? ""}`;
  const cached = sizes.get(el);
  if (cached && el.dataset.measured === key) return cached;
  const size = { w: el.offsetWidth || 120, h: el.offsetHeight || LABEL_H * 2 };
  if (el.offsetWidth > 0) {
    sizes.set(el, size);
    el.dataset.measured = key;
  }
  return size;
}

function collides(box: Box, placed: Box[]): boolean {
  return placed.some(
    (p) =>
      box.x < p.x + p.w + LABEL_GAP &&
      p.x < box.x + box.w + LABEL_GAP &&
      box.y < p.y + p.h &&
      p.y < box.y + box.h,
  );
}

function boxFor(d: DiscView, el: HTMLElement, viewW: number): Box | null {
  const { w, h } = sizeOf(el);
  if (d.state === "closed") {
    if (d.r < INSIDE_MIN_R || w > d.r * 1.9) return null;
    return { x: d.x - w / 2, y: d.y - h / 2, w, h };
  }
  if (d.state !== "open" || d.r < ABOVE_MIN_R) return null;
  let y = d.y - d.r - 4 - LABEL_H;
  if (y < 4) y = Math.max(4, d.y - d.r + 8);
  // A ring wider than the canvas keeps its name on the canvas.
  const x = Math.min(Math.max(d.x - w / 2, 4), viewW - w - 4);
  return { x, y, w, h: Math.max(h, LABEL_H) };
}

/**
 * Place each drawn area's name, biggest disc first, hiding any that would
 * land on one already placed, fall off the canvas or not fit its disc.
 * `shown` carries the visible set between frames, so only changes touch the DOM.
 */
export function placeLabels(
  field: FieldView,
  els: ReadonlyMap<string, HTMLElement>,
  view: { w: number; h: number },
  shown: Set<HTMLElement>,
): void {
  const placed: Box[] = [];
  const next = new Set<HTMLElement>();
  const discs = field.discs.filter((d) => d.state !== "slot").sort((a, b) => b.r - a.r);
  for (const d of discs) {
    const el = els.get(d.path);
    if (!el || next.has(el)) continue;
    if (el.dataset.state !== d.state) el.dataset.state = d.state;
    // One label per visual disc: an open folder its one big sub-folder fills
    // is named by that sub-folder's label instead.
    if (d.state === "open" && el.dataset.dominated === "true") continue;
    const box = boxFor(d, el, view.w);
    if (!box || box.x + box.w < 0 || box.x > view.w || box.y + box.h < 0 || box.y > view.h) continue;
    if (collides(box, placed)) continue;
    placed.push(box);
    next.add(el);
    el.style.visibility = "visible";
    el.style.transform = `translate(${Math.round(box.x)}px, ${Math.round(box.y)}px)`;
  }
  for (const el of shown) if (!next.has(el)) el.style.visibility = "hidden";
  shown.clear();
  for (const el of next) shown.add(el);
}

export function AreaLabels({
  labels,
  refs,
  selected,
  cursor,
  onSelect,
}: {
  labels: AreaLabel[];
  refs: MutableRefObject<Map<string, HTMLElement>>;
  selected: string | null;
  /** Area the keyboard cursor is on, or null when the keyboard is idle. */
  cursor: string | null;
  onSelect: (path: string) => void;
}) {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      {labels.map((l) => (
        <button
          key={l.path}
          ref={(el) => {
            if (el) refs.current.set(l.path, el);
            else refs.current.delete(l.path);
          }}
          type="button"
          tabIndex={-1}
          title={l.path || l.name}
          onClick={() => onSelect(l.path)}
          data-area={l.path}
          data-dominated={l.dominated ? "true" : undefined}
          className={`group pointer-events-auto absolute left-0 top-0 flex max-w-[220px] flex-col items-center rounded px-1.5 py-0.5 text-center leading-tight bg-[var(--color-bg-glass)] data-[state=open]:flex-row data-[state=open]:gap-1.5 ${
            cursor === l.path ? "ring-2 ring-[var(--color-accent-primary)]" : ""
          }`}
          style={{ visibility: "hidden" }}
        >
          <span
            className={`max-w-full truncate font-mono text-[10px] uppercase tracking-[0.12em] group-data-[state=closed]:text-[var(--color-text-primary)] ${
              selected === l.path
                ? "font-semibold text-[var(--color-text-primary)]"
                : "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
            }`}
          >
            {l.name}
          </span>
          {l.text ? (
            <span className="font-mono text-xs font-semibold tabular-nums text-[var(--color-text-primary)] group-data-[state=open]:text-[10px]">
              {l.text}
            </span>
          ) : null}
        </button>
      ))}
    </div>
  );
}
