/**
 * A faint graph-paper line grid under the cards. Browser code.
 *
 * Anchored to the camera so it parallaxes on pan (fixed screen spacing) and
 * reads as a designed surface. Every `GRID_MAJOR_EVERY`-th line, counted from
 * the world origin so majors stay put on pan, is drawn a touch firmer. Cheap:
 * two batched stroke passes.
 */

import type { Camera, Viewport } from "./camera";
import { GRID_MAJOR_EVERY, GRID_SPACING_PX } from "./constants";
import type { ZoomPalette } from "./theme";

const isMajor = (k: number): boolean => ((k % GRID_MAJOR_EVERY) + GRID_MAJOR_EVERY) % GRID_MAJOR_EVERY === 0;

/** Where the first on-screen line sits (px) and its world-line index, along one axis. */
function firstLine(base: number): { offset: number; index: number } {
  const sp = GRID_SPACING_PX;
  const offset = ((base % sp) + sp) % sp;
  return { offset, index: Math.round((offset - base) / sp) };
}

/** One pass of grid lines: the minor ones, or the major ones. */
function gridPass(
  ctx: CanvasRenderingContext2D,
  vp: Viewport,
  x0: { offset: number; index: number },
  y0: { offset: number; index: number },
  major: boolean,
  color: string,
): void {
  const sp = GRID_SPACING_PX;
  ctx.beginPath();
  let kx = x0.index;
  for (let x = x0.offset; x < vp.w; x += sp, kx++) {
    if (isMajor(kx) !== major) continue;
    const xx = Math.round(x) + 0.5; // crisp 1px line
    ctx.moveTo(xx, 0);
    ctx.lineTo(xx, vp.h);
  }
  let ky = y0.index;
  for (let y = y0.offset; y < vp.h; y += sp, ky++) {
    if (isMajor(ky) !== major) continue;
    const yy = Math.round(y) + 0.5;
    ctx.moveTo(0, yy);
    ctx.lineTo(vp.w, yy);
  }
  ctx.strokeStyle = color;
  ctx.lineWidth = 1;
  ctx.stroke();
}

export function drawGrid(
  ctx: CanvasRenderingContext2D,
  cam: Camera,
  vp: Viewport,
  palette: ZoomPalette,
): void {
  // Screen position of world x = 0 and y = 0.
  const x0 = firstLine(vp.w / 2 - cam.cx * cam.scale);
  const y0 = firstLine(vp.h / 2 - cam.cy * cam.scale);
  gridPass(ctx, vp, x0, y0, false, palette.grid);
  gridPass(ctx, vp, x0, y0, true, palette.gridStrong); // majors on top
}
