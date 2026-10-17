"use client";

/**
 * Moving the camera: flights, zoom steps, fitting the field, and focusing one
 * galaxy. Every way out of a focused galaxy is a state change only; the effect
 * here returns the camera, so a click, Escape and the breadcrumb all take the
 * same path.
 */

import { useCallback, useEffect, useRef } from "react";
import type { MutableRefObject } from "react";

import { frameRect, zoomAbout } from "../../zoom/camera";
import { flyDuration } from "../../zoom/camera-anim";
import { WORLD, fitCamera } from "./layout";
import type { Cam } from "./paint";
import type { Galaxy } from "./types";
import { now, type Invalidate, type LoopState } from "./use-field-canvas";

/** How far out and in the camera may go, relative to the fitted view. */
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 80;
/** One step of the zoom buttons and keys. */
export const ZOOM_STEP = 1.6;

/** Keep the camera within zoom bounds and near the field. */
export function clampCam(st: LoopState, c: Cam): Cam {
  const fit = fitCamera(st.view.w, st.view.h);
  const scale = Math.min(fit.scale * MAX_ZOOM, Math.max(fit.scale * MIN_ZOOM, c.scale));
  const lo = -WORLD * 0.25;
  const hi = WORLD * 1.25;
  return { cx: Math.min(hi, Math.max(lo, c.cx)), cy: Math.min(hi, Math.max(lo, c.cy)), scale };
}

export interface MapCamera {
  zoomBy: (factor: number) => void;
  fitView: () => void;
  /** Focus a galaxy, or leave focus with `null`. */
  focusOn: (module: string | null) => void;
  /** Leave any focus and show the whole field. */
  fitAll: () => void;
}

export function useMapCamera(opts: {
  loop: MutableRefObject<LoopState>;
  invalidate: Invalidate;
  reducedMotion: boolean;
  galaxies: Galaxy[];
  focusModule: string | null;
  setFocusModule: (module: string | null) => void;
}): MapCamera {
  const { loop, invalidate, reducedMotion, galaxies, focusModule, setFocusModule } = opts;

  /** Move the camera, flown unless the reader asked for no motion. */
  const flyTo = useCallback(
    (to: Cam, animate = true) => {
      const st = loop.current;
      const target = clampCam(st, to);
      const from = st.cam;
      st.fly = {
        from,
        to: target,
        start: now(),
        dur: animate && !reducedMotion ? flyDuration(from, target) : 0,
      };
      invalidate();
    },
    [loop, invalidate, reducedMotion],
  );

  const fitView = useCallback(() => {
    const st = loop.current;
    st.userMoved = false;
    flyTo(fitCamera(st.view.w, st.view.h));
  }, [loop, flyTo]);

  const zoomBy = useCallback(
    (factor: number) => {
      const st = loop.current;
      st.userMoved = true;
      flyTo(zoomAbout(st.cam, st.view, st.view.w / 2, st.view.h / 2, factor));
    },
    [loop, flyTo],
  );

  const focusOn = useCallback(
    (module: string | null) => {
      setFocusModule(module);
      const g = module ? galaxies.find((x) => x.module === module) : null;
      if (!g) return;
      loop.current.userMoved = true;
      flyTo(frameRect(loop.current.view, { x: g.cx - g.R, y: g.cy - g.R, w: 2 * g.R, h: 2 * g.R }, 0.9));
    },
    [galaxies, loop, flyTo, setFocusModule],
  );

  const fitAll = useCallback(() => {
    if (focusModule) setFocusModule(null);
    else fitView();
  }, [focusModule, fitView, setFocusModule]);

  // Leaving a galaxy returns the camera to the whole field.
  const lastFocus = useRef<string | null>(null);
  useEffect(() => {
    if (lastFocus.current && !focusModule) fitView();
    lastFocus.current = focusModule;
  }, [focusModule, fitView]);

  return { zoomBy, fitView, focusOn, fitAll };
}
