// Kind and role glyphs for the card. Browser code (draws to a 2D context).

import type { CardRole } from "./card-content";
import type { ZoomPalette } from "./theme";
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

/**
 * A role icon in the `[x, y, s, s]` box, in its own role ink: a flame for a
 * hotspot, a filled play triangle for an entry point, a circle-slash for dead
 * code. Quadratic curves only, so a frame's `bezierCurveTo` count stays the
 * relation count. Leaves the caller's stroke and fill state as it found it.
 */
export function drawRoleGlyph(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  s: number,
  role: CardRole,
  palette: ZoomPalette,
): void {
  // Unit-box coordinates scaled inline: no point tuples, so a frame allocates nothing.
  const X = (f: number): number => x + f * s;
  const Y = (f: number): number => y + f * s;
  ctx.save();
  ctx.lineWidth = Math.max(1, s * 0.12);
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.beginPath();
  switch (role) {
    case "hotspot": {
      ctx.strokeStyle = palette.hotspot;
      ctx.moveTo(X(0.5), Y(0.06));
      ctx.quadraticCurveTo(X(0.86), Y(0.4), X(0.82), Y(0.66));
      ctx.quadraticCurveTo(X(0.78), Y(0.94), X(0.5), Y(0.94));
      ctx.quadraticCurveTo(X(0.2), Y(0.94), X(0.18), Y(0.66));
      ctx.quadraticCurveTo(X(0.17), Y(0.48), X(0.32), Y(0.34));
      ctx.quadraticCurveTo(X(0.34), Y(0.5), X(0.44), Y(0.56));
      ctx.quadraticCurveTo(X(0.38), Y(0.3), X(0.5), Y(0.06));
      ctx.closePath();
      break;
    }
    case "entry": {
      ctx.strokeStyle = palette.entry;
      ctx.fillStyle = palette.entry;
      ctx.moveTo(X(0.26), Y(0.16));
      ctx.lineTo(X(0.84), Y(0.5));
      ctx.lineTo(X(0.26), Y(0.84));
      ctx.closePath();
      ctx.fill();
      break;
    }
    case "dead": {
      ctx.strokeStyle = palette.dead;
      ctx.arc(X(0.5), Y(0.5), 0.38 * s, 0, Math.PI * 2);
      ctx.moveTo(X(0.23), Y(0.23));
      ctx.lineTo(X(0.77), Y(0.77));
      break;
    }
  }
  ctx.stroke();
  ctx.restore();
}
