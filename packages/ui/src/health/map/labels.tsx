"use client";

/**
 * Galaxy names: real-px HTML over the canvas, crisp at any zoom.
 *
 * React renders one button per galaxy once per field; the frame loop places
 * them with {@link placeLabels}, writing styles directly, so a pan moves the
 * names without a React render. A name is also the way into its galaxy.
 */

import type { MutableRefObject } from "react";
import type { Cam } from "./paint";
import type { Galaxy } from "./types";

/** Label a galaxy once its on-screen radius clears this (px). */
const LABEL_MIN_SCREEN_R = 28;
/** A field this small names every galaxy, however small. */
const NAME_ALL_BELOW = 12;
/** Rough label box, for collision culling before a label has been measured. */
const LABEL_H = 18;
/** Clear space kept between two names. */
const LABEL_GAP = 6;

const labelWidth = (el: HTMLElement | null, text: string) =>
  el?.offsetWidth || Math.min(240, text.length * 6.6 + 14);

interface Box {
  x: number;
  y: number;
  w: number;
}

export interface PlaceLabelsOptions {
  galaxies: { cx: number; cy: number; R: number; label: string }[];
  /** Galaxy indices, biggest first: the order names claim space in. */
  order: number[];
  /** Index of the focused galaxy, or -1. */
  focus: number;
  camera: Cam;
  view: { w: number; h: number };
  els: (HTMLElement | null)[];
}

/** Whether a galaxy is big enough on screen to carry its name. */
function bigEnough(i: number, R: number, focus: number, minR: number): boolean {
  if (i === focus) return true;
  if (R < minR) return false;
  // Inside a focused galaxy only its larger neighbours keep a name.
  return focus < 0 || R >= minR * 2;
}

function onCanvas(box: Box, view: { w: number; h: number }): boolean {
  return box.x + box.w > 0 && box.x < view.w && box.y + LABEL_H > 0 && box.y < view.h;
}

function collides(box: Box, placed: Box[]): boolean {
  return placed.some(
    (p) =>
      box.x < p.x + p.w + LABEL_GAP &&
      p.x < box.x + box.w + LABEL_GAP &&
      Math.abs(p.y - box.y) < LABEL_H + 2,
  );
}

/** Where a galaxy's name sits: centred just above its ring. */
function labelBox(g: PlaceLabelsOptions["galaxies"][number], el: HTMLElement, o: PlaceLabelsOptions): Box {
  const { camera, view } = o;
  const x = view.w / 2 + (g.cx - camera.cx) * camera.scale;
  let y = view.h / 2 + (g.cy - g.R - camera.cy) * camera.scale - 4 - LABEL_H;
  // A ring whose top is off the canvas carries its name just inside it.
  if (y < 4) y = Math.max(4, y + LABEL_H + 10);
  const w = labelWidth(el, g.label);
  return { x: x - w / 2, y, w };
}

/**
 * Put each galaxy's name just above its ring, biggest galaxy first, and hide
 * any name that would land on one already placed or whose galaxy is too small
 * on screen to need one.
 */
export function placeLabels(o: PlaceLabelsOptions): void {
  const placed: Box[] = [];
  const minR = o.galaxies.length <= NAME_ALL_BELOW ? 4 : LABEL_MIN_SCREEN_R;
  for (const i of o.order) {
    const g = o.galaxies[i];
    const el = o.els[i];
    if (!g || !el) continue;
    const box = labelBox(g, el, o);
    const visible =
      bigEnough(i, g.R * o.camera.scale, o.focus, minR) &&
      onCanvas(box, o.view) &&
      !collides(box, placed);
    if (visible) placed.push(box);
    el.style.visibility = visible ? "visible" : "hidden";
    el.style.transform = `translate(${Math.round(box.x)}px, ${Math.round(box.y)}px)`;
  }
}

export function GalaxyLabels({
  galaxies,
  refs,
  focusIdx,
  cursorIdx,
  onToggle,
}: {
  galaxies: Galaxy[];
  refs: MutableRefObject<(HTMLButtonElement | null)[]>;
  focusIdx: number;
  /** Galaxy the keyboard cursor is on, or -1 when the keyboard is idle. */
  cursorIdx: number;
  onToggle: (module: string) => void;
}) {
  return (
    <div className="pointer-events-none absolute inset-0">
      {galaxies.map((g, i) => (
        <button
          key={g.module}
          ref={(el) => {
            refs.current[i] = el;
          }}
          type="button"
          tabIndex={-1}
          title={`${g.label} · ${g.fileCount.toLocaleString()} files`}
          onClick={() => onToggle(g.module)}
          className={`pointer-events-auto absolute left-0 top-0 max-w-[240px] truncate rounded bg-[var(--color-bg-glass)] px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] ${
            cursorIdx === i ? "ring-2 ring-[var(--color-accent-primary)]" : ""
          } ${focusIdx === i ? "font-medium text-[var(--color-text-primary)]" : ""}`}
          style={{ visibility: "hidden" }}
        >
          {g.label}
        </button>
      ))}
    </div>
  );
}
