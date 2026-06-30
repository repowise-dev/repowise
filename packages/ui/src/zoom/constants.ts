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
 * Per-parent grid layout. Children are placed in a near-uniform grid of cells
 * (columns/rows track the parent's world aspect so cells stay near-square), each
 * box separated from its neighbours by a whitespace channel. This replaces the
 * area-filling treemap so the canvas reads as a diagram of discrete boxes, not a
 * subdivided heatmap.
 */
export const GRID_GUTTER_FRACTION = 0.26; // whitespace channel as a fraction of each cell axis
export const IMPORTANCE_SCALE_MIN = 0.86; // least-important box shrinks only this far (boxes stay near-uniform)

/**
 * Faint dot-grid painted under the cards (anchored to the camera so it parallaxes
 * like a real surface). Spacing in screen px; the dots stay subtle via the low
 * alpha of `--color-canvas-dot`.
 */
export const DOT_GRID_SPACING_PX = 30;
export const DOT_GRID_RADIUS_PX = 1;

/**
 * Relations are revealed on demand, not drawn all at once: only the edges
 * touching the hovered or selected box appear, so the canvas stays calm and the
 * arrows you do see are the ones you asked for.
 */
export const EDGE_MAX_PER_PARENT = 24; // cap arrows considered per zoom level
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
export const EDGE_CONTROL_PROJECTION = 0.4; // bezier control offset as a fraction of the endpoint gap
export const EDGE_SLOT_GAP_FRACTION = 0.18; // slot spacing as a fraction of the box side length
export const EDGE_MAX_SLOTS = 5; // distinct anchor slots per box side
