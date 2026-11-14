// External-system glyph for the dashed C4 cards. Browser code (draws to a 2D context).

import type { ZoomExternalKind } from "./types";

/**
 * A monoline glyph per external kind, in the `[x, y, s, s]` box and the
 * caller's stroke style, matching `drawKindGlyph`'s weight: a cylinder for a
 * database, a globe for a network client, a cloud for a hosted service, and a
 * package box for anything else.
 */
export function drawExternalGlyph(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  s: number,
  kind: ZoomExternalKind,
): void {
  const cx = x + s / 2;
  ctx.beginPath();
  switch (kind) {
    case "db": {
      const rx = s * 0.36;
      const ry = s * 0.12;
      const top = y + s * 0.2;
      const bottom = y + s * 0.8;
      ctx.ellipse(cx, top, rx, ry, 0, 0, Math.PI * 2);
      ctx.moveTo(cx - rx, top);
      ctx.lineTo(cx - rx, bottom);
      ctx.ellipse(cx, bottom, rx, ry, 0, Math.PI, 0, true);
      ctx.lineTo(cx + rx, top);
      break;
    }
    case "network": {
      const cy = y + s / 2;
      const r = s * 0.38;
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.moveTo(cx + r * 0.42, cy);
      ctx.ellipse(cx, cy, r * 0.42, r, 0, 0, Math.PI * 2);
      ctx.moveTo(cx - r, cy);
      ctx.lineTo(cx + r, cy);
      break;
    }
    case "service": {
      // Cloud: three bumps over a flat base.
      const base = y + s * 0.72;
      ctx.moveTo(x + s * 0.22, base);
      ctx.arc(x + s * 0.28, y + s * 0.58, s * 0.14, Math.PI * 0.6, Math.PI * 1.5);
      ctx.arc(x + s * 0.48, y + s * 0.42, s * 0.2, Math.PI * 1.1, Math.PI * 1.9);
      ctx.arc(x + s * 0.72, y + s * 0.58, s * 0.14, Math.PI * 1.5, Math.PI * 0.4);
      ctx.closePath();
      break;
    }
    default: {
      // Package: a box with its lid seam.
      ctx.rect(x + s * 0.16, y + s * 0.28, s * 0.68, s * 0.56);
      ctx.moveTo(x + s * 0.16, y + s * 0.44);
      ctx.lineTo(x + s * 0.84, y + s * 0.44);
      ctx.moveTo(cx, y + s * 0.28);
      ctx.lineTo(cx, y + s * 0.44);
    }
  }
  ctx.stroke();
}
