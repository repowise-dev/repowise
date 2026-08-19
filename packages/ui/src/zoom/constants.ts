/**
 * Tunable constants for the continuous-zoom canvas.
 *
 * Kept in one browser-free module so the renderer and the pure math share a
 * single source of truth and the unit tests can assert against them.
 */

/** Fade thresholds scale with canvas width so the feel is resolution-independent. */
export const START_FRACTION = 0.25; // fade begins when a node spans this fraction of width
export const END_FRACTION = 0.4; // fade completes (children fully in) at this fraction
export const START_MIN_PX = 80;
export const START_MAX_PX = 450;
export const END_MIN_PX = 200;
export const END_MAX_PX = 640;

/** A node narrower than this many screen pixels is not worth drawing. */
export const MIN_DRAW_PX = 2;

/** Below this inherited alpha a subtree is fully transparent: skip it. */
export const ALPHA_EPSILON = 0.01;

/** Camera zoom (world units -> screen pixels) is clamped to this range. */
export const MIN_SCALE = 0.05;
export const MAX_SCALE = 4_000_000;

/**
 * Safety ceiling on children drawn per parent. With the uniform grid layout the
 * cells appear together as the parent grows (no per-frame top-N selection, which
 * left holes when only the highest-ranked cells of a fixed grid were drawn);
 * instead every child is laid out and the natural level-of-detail bounds the
 * live count: a subtree is skipped once it is off-screen (`isOnScreen`) or
 * sub-pixel (`MIN_DRAW_PX`). This ceiling only bites a pathologically flat
 * folder (hundreds of immediate children), where the least-important tail is
 * dropped by `sibling_rank` so the frame stays cheap.
 */
export const MAX_CHILDREN_DRAWN = 320;

/** Off-screen culling keeps a margin (in screen px) so panning has no pop-in. */
export const CULL_MARGIN_PX = 64;

/**
 * Per-parent masonry pack. Children are sized by importance (bigger = more
 * central) and row-packed into centred, variable-width rows, so the canvas reads
 * as an organic architecture map rather than a rigid lattice. Cards keep a fixed
 * landscape aspect (readable text); only their scale varies. Tuning:
 * - CARD_ASPECT: width:height of every card.
 * - PACK_SIZE_MIN/MAX: card height (relative units) for the least / most
 *   important sibling; the ratio sets how dramatic the size variation is.
 * - PACK_IMPORTANCE_GAMMA: <1 lifts small cards so minor nodes stay readable.
 * - PACK_GUTTER: whitespace between cards (same relative units); generous, to
 *   give the on-hover relation arrows room to breathe.
 */
export const CARD_ASPECT = 1.5;
export const PACK_SIZE_MIN = 0.58;
export const PACK_SIZE_MAX = 1.0;
export const PACK_IMPORTANCE_GAMMA = 0.7;
export const PACK_GUTTER = 0.3;

/**
 * Graph-paper line grid painted under the cards (anchored to the camera so it
 * parallaxes like a real surface, at a fixed screen spacing). Every
 * `GRID_MAJOR_EVERY`-th line is a firmer "major" line for depth; both stay subtle
 * via the low alpha of `--color-zoom-grid` / `--color-zoom-grid-strong`.
 */
export const GRID_SPACING_PX = 32;
export const GRID_MAJOR_EVERY = 4;

/**
 * Relations are revealed on demand, not drawn all at once: only the edges
 * touching the hovered or selected box appear, so the canvas stays calm and the
 * arrows you do see are the ones you asked for.
 */
export const EDGE_MAX_PER_PARENT = 10; // cap arrows per zoom level (only the strongest incident relations show, keeping it calm)
export const EDGE_MIN_BOX_PX = 40; // skip edges to boxes smaller than this on screen
export const ARROW_MIN_BOX_PX = 96; // suppress the arrowhead below this box size
export const ARROW_SIZE_PX = 7; // arrowhead length in screen px
export const EDGE_LINE_PX = 1.25; // edge stroke width in screen px
export const EDGE_FOCUS_ALPHA = 0.95; // relations touching the focused node

/**
 * Edge-routing tunables. Each connector leaves the source box from the side that
 * faces the target, curves outward (control points projected along the side
 * normal so the line bows around intervening boxes), and several edges sharing a
 * side fan out into distinct slots so they never collapse onto one another.
 */
export const EDGE_CONTROL_PROJECTION = 0.55; // bezier control offset as a fraction of the endpoint gap (higher = more bow, edges clear the boxes)
export const EDGE_SLOT_GAP_FRACTION = 0.26; // slot spacing as a fraction of the box side length (higher = fan spreads wider)
export const EDGE_MAX_SLOTS = 6; // distinct anchor slots per box side
