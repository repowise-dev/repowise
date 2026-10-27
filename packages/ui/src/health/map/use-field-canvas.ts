"use client";

/**
 * The map's two canvases and the loop that paints them.
 *
 * Everything a frame reads lives in one mutable loop state, so a frame never
 * waits on React and React never re-renders for a frame. Callers mark which
 * layer changed with `invalidate`; a frame decides the semantic zoom for the
 * current camera ({@link computeView}), paints only the layers that changed,
 * and keeps scheduling itself only while a camera flight or a lens recolor is
 * running. Once the camera rests, the frame tells the host what the view wants
 * loaded next.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { MutableRefObject, RefObject } from "react";

import { useThemeVersion } from "../../shared/use-theme-tokens";
import { easeInOutCubic, interpolateCamera } from "../../zoom/camera-anim";
import { isLoudFill } from "../tokens";
import type { AreaIndex } from "./areas";
import type { AreaGeometry } from "./area-layout";
import { computeView, type DiscView, type FieldView } from "./area-view";
import { NEUTRAL_FILL } from "./lens";
import { WORLD, fitCamera } from "./layout";
import { placeLabels } from "./labels";
import {
  attachPaths,
  createColourResolver,
  drawField,
  drawMarks,
  groupBatches,
  resolvePalette,
  type Batch,
  type Cam,
  type FieldScene,
  type Frame,
  type Palette,
  type Rgb,
  type View,
} from "./paint";

/** Smallest radius a node is drawn at, so a zoomed-out file is still a dot. */
export const MIN_NODE_PX = 0.9;
/** Lens recolor, in ms. */
const COLOUR_TWEEN_MS = 320;

export function now(): number {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

export interface Tween {
  from: Cam;
  to: Cam;
  start: number;
  dur: number;
}

/** What the view asks the host to load, reported once the camera rests. */
export interface Wants {
  children: string[];
  files: string[];
}

/** What the frame loop paints, refreshed by the component on every render. */
export interface FieldLive {
  index: AreaIndex;
  geo: AreaGeometry;
  scene: FieldScene;
  fills: string[];
  areaFill: ReadonlyMap<string, string>;
  marked: number[];
  selectedIdx: number;
  cursorIdx: number;
  hoverIdx: number;
  selectedArea: string | null;
  hoverArea: string | null;
  cursorArea: string | null;
  minNodeR: number;
  filesCovered: (path: string) => boolean;
  onWants?: ((wants: Wants) => void) | undefined;
}

export interface Dirty {
  base: boolean;
  top: boolean;
  labels: boolean;
}

/** The loop's whole mutable state. Other hooks move the camera through it. */
export interface LoopState {
  view: View;
  cam: Cam;
  /** The reader moved the camera, so a resize keeps their view. */
  userMoved: boolean;
  fly: Tween | null;
  colour: { from: string[]; start: number } | null;
  shownFills: string[] | null;
  batches: Batch[];
  stale: boolean;
  /** The dot floor the current paths were built with, 0 for none. */
  floor: number;
  dirty: Dirty;
  raf: number | null;
  resolve: (css: string) => Rgb;
  palette: Palette | null;
  /** The semantic zoom the last frame drew, for picking. */
  field: FieldView | null;
  /** Last frame's draw time in ms, for the perf readout. */
  frameMs: number;
}

export type Invalidate = (what?: Partial<Dirty>) => void;

export interface FieldCanvas {
  loop: MutableRefObject<LoopState>;
  invalidate: Invalidate;
  /** The canvas has a size, so labels can be placed. */
  ready: boolean;
}

const ALL: Dirty = { base: true, top: true, labels: true };

function initialLoop(): LoopState {
  return {
    view: { w: 0, h: 0, dpr: 1 },
    cam: { cx: WORLD / 2, cy: WORLD / 2, scale: 1 },
    userMoved: false,
    fly: null,
    colour: null,
    shownFills: null,
    batches: [],
    stale: true,
    floor: 0,
    dirty: { ...ALL },
    raf: null,
    resolve: () => [128, 128, 128],
    palette: null,
    field: null,
    frameMs: 0,
  };
}

/** Advance a camera flight. True when the camera moved this frame. */
function stepFly(st: LoopState, t0: number): { moved: boolean; animating: boolean } {
  const f = st.fly;
  if (!f) return { moved: false, animating: false };
  const p = f.dur <= 0 ? 1 : (t0 - f.start) / f.dur;
  st.cam = p >= 1 ? f.to : interpolateCamera(f.from, f.to, p);
  if (p >= 1) st.fly = null;
  return { moved: true, animating: p < 1 };
}

/** Advance a lens recolor. `t` is its eased progress, 1 at rest. */
function stepColour(st: LoopState, t0: number): { t: number; animating: boolean } {
  if (!st.colour) return { t: 1, animating: false };
  st.dirty.base = true;
  const p = (t0 - st.colour.start) / COLOUR_TWEEN_MS;
  if (p < 1) return { t: easeInOutCubic(p), animating: true };
  st.colour = null;
  st.stale = true;
  return { t: 1, animating: false };
}

/**
 * The dot floor this zoom needs, in world units, or 0 when no node is small
 * enough to need one. A zoom step that moves the floor far enough rebuilds
 * the paths; otherwise they are reused across every frame of a pan or zoom.
 */
function floorFor(st: LoopState, live: FieldLive): number {
  const s = st.cam.scale;
  const needed = live.minNodeR * s < MIN_NODE_PX * 1.5 ? MIN_NODE_PX / s : 0;
  const built = st.floor;
  const drift = built > 0 && needed > 0 ? needed / built : 1;
  if ((needed > 0) !== (built > 0) || drift > 1.25 || drift < 0.8) st.stale = true;
  return needed;
}

function rebuild(st: LoopState, live: FieldLive, floor: number): void {
  st.batches = groupBatches(live.scene, live.fills, st.colour?.from ?? null, isLoudFill, st.resolve);
  attachPaths(st.batches, live.scene, floor);
  st.floor = floor;
  st.stale = false;
  st.dirty.base = true;
}

interface Layers {
  base: RefObject<HTMLCanvasElement | null>;
  top: RefObject<HTMLCanvasElement | null>;
  labels: MutableRefObject<Map<string, HTMLElement>>;
  shownLabels: Set<HTMLElement>;
}

const discOf = (field: FieldView, path: string | null): DiscView | null =>
  path === null ? null : (field.discs.find((d) => d.path === path && d.state !== "slot") ?? null);

/** The semantic zoom for this camera: recomputed when the camera or the field moved. */
function decideField(st: LoopState, live: FieldLive, moved: boolean): FieldView {
  if (!moved && !st.dirty.base && st.field) return st.field;
  return computeView({
    index: live.index,
    geo: live.geo,
    hosts: live.scene.hosts,
    hostIndex: live.scene.hostIndex,
    filesCovered: live.filesCovered,
    cam: st.cam,
    view: st.view,
  });
}

/** The top layer's rings, for what is selected, hovered, under the cursor or matched. */
function paintMarks(top: CanvasRenderingContext2D, st: LoopState, live: FieldLive, frame: Frame): void {
  const { field } = frame;
  drawMarks(top, frame, {
    selected: live.selectedIdx,
    hovered: live.hoverIdx,
    cursor: live.cursorIdx,
    matches: live.marked,
    selectedArea: discOf(field, live.selectedArea),
    hoveredArea: discOf(field, live.hoverArea),
    cursorArea: discOf(field, live.cursorArea),
    fillOf: (i) => st.resolve(live.fills[i] ?? NEUTRAL_FILL),
    floorPx: MIN_NODE_PX,
  });
}

/** Once the camera rests on a new view, tell the host what it wants loaded. */
function reportWants(live: FieldLive, field: FieldView, settled: boolean): void {
  const wants = field.wantChildren.length > 0 || field.wantFiles.length > 0;
  if (settled && wants) live.onWants?.({ children: field.wantChildren, files: field.wantFiles });
}

/** One frame: advance the tweens, rebuild what went stale, paint what changed. */
function runFrame(st: LoopState, live: FieldLive, layers: Layers): boolean {
  const t0 = now();
  const flight = stepFly(st, t0);
  const colour = stepColour(st, t0);
  const floor = floorFor(st, live);
  const base = layers.base.current?.getContext("2d") ?? null;
  const top = layers.top.current?.getContext("2d") ?? null;
  st.palette ??= resolvePalette(st.resolve);
  if (st.stale && base) rebuild(st, live, floor);

  const field = decideField(st, live, flight.moved);
  const fieldChanged = field !== st.field;
  st.field = field;
  const frame: Frame = {
    scene: live.scene,
    cam: st.cam,
    view: st.view,
    palette: st.palette,
    field,
    areaRgb: (path) => st.resolve(live.areaFill.get(path) ?? NEUTRAL_FILL),
  };
  if (base && (st.dirty.base || flight.moved)) drawField(base, frame, st.batches, colour.t);
  if (top && (st.dirty.top || fieldChanged)) paintMarks(top, st, live, frame);
  if (st.dirty.labels || fieldChanged) placeLabels(field, layers.labels.current, st.view, layers.shownLabels);
  st.dirty = { base: false, top: false, labels: false };
  st.frameMs = now() - t0;
  const animating = flight.animating || colour.animating;
  reportWants(live, field, !animating && fieldChanged);
  return animating;
}

/** Size both canvases to `w` by `h` CSS px at the current pixel ratio. */
function sizeCanvases(st: LoopState, layers: Layers, w: number, h: number): void {
  const dpr = typeof window !== "undefined" ? Math.min(window.devicePixelRatio || 1, 3) : 1;
  st.view = { w, h, dpr };
  for (const c of [layers.base.current, layers.top.current]) {
    if (!c) continue;
    c.width = Math.max(1, Math.round(w * dpr));
    c.height = Math.max(1, Math.round(h * dpr));
    c.style.width = `${w}px`;
    c.style.height = `${h}px`;
  }
  if (!st.userMoved) {
    st.fly = null;
    st.cam = fitCamera(w, h);
  }
}

/**
 * Track the container's box, coalesced to one resize per frame, and the
 * screen's pixel ratio, which a ResizeObserver does not report: the query
 * names the current ratio, so it is re-armed on every change.
 */
function useCanvasSize(
  container: RefObject<HTMLElement | null>,
  layers: Layers,
  loop: MutableRefObject<LoopState>,
  minHeight: number,
  invalidate: Invalidate,
  setReady: (ready: boolean) => void,
): void {
  useEffect(() => {
    const node = container.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    let pending: { width: number; height: number } | null = null;
    let last = { width: 0, height: 0 };
    let sizeRaf: number | null = null;
    const apply = () => {
      sizeRaf = null;
      if (!pending) return;
      last = pending;
      pending = null;
      const w = Math.round(last.width);
      sizeCanvases(loop.current, layers, w, Math.round(Math.max(last.height, minHeight)));
      setReady(w > 0);
      invalidate();
    };
    const observer = new ResizeObserver((entries) => {
      const first = entries[0];
      if (!first) return;
      pending = { width: first.contentRect.width, height: first.contentRect.height };
      // The first size lands synchronously so the field never paints a blank
      // frame at mount; later ones coalesce into one per animation frame.
      if (loop.current.view.w === 0) apply();
      else if (sizeRaf == null) sizeRaf = requestAnimationFrame(apply);
    });
    observer.observe(node);
    let media: MediaQueryList | null = null;
    const onRatio = () => {
      pending = last;
      apply();
      watchRatio();
    };
    const watchRatio = () => {
      media?.removeEventListener("change", onRatio);
      if (typeof window === "undefined" || !window.matchMedia) return;
      media = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`);
      media.addEventListener("change", onRatio);
    };
    watchRatio();
    return () => {
      observer.disconnect();
      media?.removeEventListener("change", onRatio);
      if (sizeRaf != null) cancelAnimationFrame(sizeRaf);
    };
  }, [container, layers, loop, minHeight, invalidate, setReady]);
}

/** Each input names the one layer it changes. */
function useRepaintTriggers(
  container: RefObject<HTMLElement | null>,
  loop: MutableRefObject<LoopState>,
  live: FieldLive,
  reducedMotion: boolean,
  labelKey: unknown,
  ready: boolean,
  invalidate: Invalidate,
): void {
  const themeVersion = useThemeVersion();
  const { scene, fills, areaFill, geo, marked, selectedIdx, cursorIdx, hoverIdx } = live;
  const { selectedArea, hoverArea, cursorArea } = live;
  useEffect(() => {
    const st = loop.current;
    st.resolve = createColourResolver(container.current);
    st.palette = null;
    st.stale = true;
    invalidate();
  }, [themeVersion, container, loop, invalidate]);

  useEffect(() => {
    // New files are a new set of paths: no tween from the old colours.
    const st = loop.current;
    st.shownFills = null;
    st.colour = null;
    st.stale = true;
    invalidate();
  }, [scene, loop, invalidate]);

  useEffect(() => {
    const st = loop.current;
    const previous = st.shownFills;
    st.shownFills = fills;
    if (previous && previous !== fills && previous.length === fills.length && !reducedMotion) {
      st.colour = { from: previous, start: now() };
    }
    st.stale = true;
    invalidate();
  }, [fills, reducedMotion, loop, invalidate]);

  // A finished or failed load changes what the view should ask for next.
  useEffect(() => {
    invalidate();
  }, [areaFill, geo, live.filesCovered, invalidate]);

  useEffect(() => {
    invalidate({ top: true });
  }, [marked, selectedIdx, cursorIdx, hoverIdx, selectedArea, hoverArea, cursorArea, invalidate]);

  useEffect(() => {
    invalidate({ labels: true });
  }, [labelKey, ready, invalidate]);
}

export function useFieldCanvas(opts: {
  container: RefObject<HTMLElement | null>;
  base: RefObject<HTMLCanvasElement | null>;
  top: RefObject<HTMLCanvasElement | null>;
  labels: MutableRefObject<Map<string, HTMLElement>>;
  minHeight: number;
  live: FieldLive;
  reducedMotion: boolean;
  /** Changes whenever the labels need re-placing without a camera move. */
  labelKey: unknown;
}): FieldCanvas {
  const { container, base, top, labels, minHeight, live, reducedMotion, labelKey } = opts;
  const loop = useRef<LoopState>(initialLoop());
  const liveRef = useRef(live);
  liveRef.current = live;
  const layers = useRef<Layers>({ base, top, labels, shownLabels: new Set() }).current;
  const [ready, setReady] = useState(false);

  const frame = useCallback(() => {
    const st = loop.current;
    st.raf = null;
    if (st.view.w === 0) return;
    if (runFrame(st, liveRef.current, layers)) st.raf = requestAnimationFrame(frame);
  }, [layers]);

  const invalidate = useCallback<Invalidate>(
    (what = ALL) => {
      const st = loop.current;
      if (what.base) st.dirty.base = true;
      if (what.top) st.dirty.top = true;
      if (what.labels) st.dirty.labels = true;
      if (st.raf == null && typeof requestAnimationFrame !== "undefined") {
        st.raf = requestAnimationFrame(frame);
      }
    },
    [frame],
  );

  useEffect(
    () => () => {
      // Cleared as well as cancelled: a remount (StrictMode runs effects
      // twice) must be able to schedule again.
      const st = loop.current;
      if (st.raf != null) cancelAnimationFrame(st.raf);
      st.raf = null;
    },
    [],
  );

  useCanvasSize(container, layers, loop, minHeight, invalidate, setReady);
  useRepaintTriggers(container, loop, live, reducedMotion, labelKey, ready, invalidate);
  return { loop, invalidate, ready };
}
