/**
 * Shared ruled-paper card texture for the zoom canvas. Browser code.
 *
 * The KG node cards (`c4/nodes/ink-node-shell.tsx`) get their tactile "ruled
 * paper" look from a real photo, `packages/web/public/kg-card-paper.jpg`, applied
 * as a CSS `background-image` under a translucent wash (see `--kg-card-texture` in
 * globals.css). The zoom map is a `<canvas>`, which cannot consume a CSS
 * background-image, so we load the same asset and expose it as a `CanvasPattern`
 * for `drawCard` to paint under a matching per-theme wash. Reusing the one asset
 * keeps the two surfaces visually identical, so the zoom map can stand in for the
 * KG without a second texture to maintain.
 *
 * The load is async; `onReady` fires once the image is decoded so the renderer can
 * repaint the (already-drawn) first frame with the texture in place.
 */

/** Served from `packages/web/public`; consumers must ship this asset too. */
const PAPER_SRC = "/kg-card-paper.jpg";

export class PaperTexture {
  private img: HTMLImageElement | null = null;
  private pattern: CanvasPattern | null = null;
  private loaded = false;

  constructor(onReady: () => void) {
    // SSR / no-DOM: stay inert. `get()` returns null and cards fall back to a
    // flat fill, so the canvas still renders without the texture.
    if (typeof Image === "undefined") return;
    const img = new Image();
    img.onload = () => {
      this.img = img;
      this.loaded = true;
      onReady();
    };
    img.src = PAPER_SRC;
  }

  /**
   * The tiling pattern, built lazily against the drawing context and cached. The
   * tile is anchored to the canvas (screen space), so cards slide over a
   * continuous paper sheet; camera-anchored parallax (like the dot-grid) is a
   * possible future refinement.
   */
  get(ctx: CanvasRenderingContext2D): CanvasPattern | null {
    if (!this.loaded || !this.img) return null;
    if (!this.pattern) this.pattern = ctx.createPattern(this.img, "repeat");
    return this.pattern;
  }
}
