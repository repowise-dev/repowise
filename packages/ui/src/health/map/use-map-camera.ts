"use client";

/**
 * Moving the camera: flights, zoom steps, and framing the selected area.
 *
 * The camera follows the selection. Selecting an area is a state change only
 * (the host keeps it in the URL); the effect here flies to whatever area is
 * selected once its disc is laid out, so a click, a breadcrumb, Backspace,
 * Escape, a deep link and the browser's back button all take the same path.
 */

import { useCallback, useEffect, useRef } from "react";
import type { MutableRefObject } from "react";

import { frameRect, zoomAbout } from "../../zoom/camera";
import { flyDuration } from "../../zoom/camera-anim";
import { ROOT_AREA } from "../../lib/areas";
import type { AreaGeometry } from "./area-layout";
import { WORLD, fitCamera } from "./layout";
import type { Cam } from "./paint";
import { now, type Invalidate, type LoopState } from "./use-field-canvas";

/** How far out and in the camera may go, relative to the fitted view. */
const MIN_ZOOM = 0.5;
/** Deep enough for a leaf ten directories down. */
const MAX_ZOOM = 20_000;
/** One step of the zoom buttons and keys. */
export const ZOOM_STEP = 1.6;
/** How much of the shorter side a framed area fills. */
const FRAME_FILL = 0.9;

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
  /** Frame the selected area again, after the reader panned away. */
  reframe: () => void;
}

export function useMapCamera(opts: {
  loop: MutableRefObject<LoopState>;
  invalidate: Invalidate;
  reducedMotion: boolean;
  geo: AreaGeometry;
  /** The selected area; the camera frames it. */
  area: string;
  ready: boolean;
}): MapCamera {
  const { loop, invalidate, reducedMotion, geo, area, ready } = opts;

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

  const frameArea = useCallback(
    (path: string, animate: boolean) => {
      const st = loop.current;
      if (st.view.w === 0) return false;
      if (path === ROOT_AREA) {
        st.userMoved = false;
        flyTo(fitCamera(st.view.w, st.view.h), animate);
        return true;
      }
      const c = geo.circles.get(path);
      if (!c) return false;
      st.userMoved = true;
      flyTo(frameRect(st.view, { x: c.x - c.r, y: c.y - c.r, w: 2 * c.r, h: 2 * c.r }, FRAME_FILL), animate);
      return true;
    },
    [geo, loop, flyTo],
  );

  // Fly to the selection whenever it changes, and once more when a deep-linked
  // area's disc first appears. The first framing jumps rather than flies, so a
  // shared link opens where it points instead of animating in from the root.
  const framed = useRef<string | null>(null);
  useEffect(() => {
    if (!ready || framed.current === area) return;
    if (frameArea(area, framed.current !== null)) framed.current = area;
  }, [area, ready, frameArea]);

  const zoomBy = useCallback(
    (factor: number) => {
      const st = loop.current;
      st.userMoved = true;
      flyTo(zoomAbout(st.cam, st.view, st.view.w / 2, st.view.h / 2, factor));
    },
    [loop, flyTo],
  );

  const reframe = useCallback(() => {
    frameArea(area, true);
  }, [area, frameArea]);

  return { zoomBy, reframe };
}
