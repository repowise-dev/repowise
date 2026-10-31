// Kind glyph for the card header. Browser code (draws to a 2D context).

import type { ZoomKind } from "./types";

/** Cell offsets of the "system" glyph's 2x2 grid, hoisted so a draw allocates nothing. */
const GRID_CELLS = [0.1, 0.56] as const;

/**
 * A minimal monoline glyph per node kind, drawn into the `[x, y, s, s]` box in the
 * caller's current stroke style. Canvas-native rather than the KG cards' Lucide
 * components (which cannot be drawn to a 2D context), but echoes the same visual
 * language: stacked planes = layer, folder = folder/group, a page = file, a grid
 * of boxes = the whole system.
 */
export function drawKindGlyph(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  s: number,
  kind: ZoomKind,
): void {
  const at = (fx: number, fy: number): [number, number] => [x + fx * s, y + fy * s];
  ctx.beginPath();
  switch (kind) {
    case "system": {
      // 2x2 grid of small boxes.
      const d = 0.34 * s;
      for (const gx of GRID_CELLS) {
        for (const gy of GRID_CELLS) ctx.rect(x + gx * s, y + gy * s, d, d);
      }
      break;
    }
    case "layer": {
      // Two stacked planes (Lucide "Layers" idiom).
      ctx.moveTo(...at(0.5, 0.1));
      ctx.lineTo(...at(0.88, 0.32));
      ctx.lineTo(...at(0.5, 0.54));
      ctx.lineTo(...at(0.12, 0.32));
      ctx.closePath();
      ctx.moveTo(...at(0.12, 0.52));
      ctx.lineTo(...at(0.5, 0.74));
      ctx.lineTo(...at(0.88, 0.52));
      break;
    }
    case "group": {
      // Two overlapping cards (a collection).
      ctx.rect(x + 0.34 * s, y + 0.14 * s, 0.44 * s, 0.44 * s);
      ctx.rect(x + 0.14 * s, y + 0.36 * s, 0.44 * s, 0.44 * s);
      break;
    }
    case "folder": {
      // Classic tabbed folder.
      ctx.moveTo(...at(0.14, 0.3));
      ctx.lineTo(...at(0.42, 0.3));
      ctx.lineTo(...at(0.5, 0.42));
      ctx.lineTo(...at(0.86, 0.42));
      ctx.lineTo(...at(0.86, 0.82));
      ctx.lineTo(...at(0.14, 0.82));
      ctx.closePath();
      break;
    }
    case "file": {
      // Page with a folded corner.
      ctx.moveTo(...at(0.24, 0.08));
      ctx.lineTo(...at(0.6, 0.08));
      ctx.lineTo(...at(0.78, 0.26));
      ctx.lineTo(...at(0.78, 0.92));
      ctx.lineTo(...at(0.24, 0.92));
      ctx.closePath();
      ctx.moveTo(...at(0.6, 0.08));
      ctx.lineTo(...at(0.6, 0.26));
      ctx.lineTo(...at(0.78, 0.26));
      break;
    }
  }
  ctx.stroke();
}
