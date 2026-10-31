/**
 * What a card shows at a given on-screen size, and how much room its name
 * needs. Pure, browser-free and unit-tested; `card-text.ts` paints it and
 * `layout.ts` sizes cards with the same font rule, so the pack leaves room for
 * exactly the label the renderer will draw.
 *
 * A card reads like a C4 box: a header (kind glyph + name, up to two lines),
 * a meta row of technology chips, a body (the summary, wrapped to the space
 * left), and a footer (health dot, kind or badge, one metric). Detail is
 * disclosed by size tier so a small card never crams.
 */

import { nodeRoles } from "./node-signals";
import { techName } from "./tech-names";
import { tokenize, type WrapMode } from "./text";
import type { ZoomKind, ZoomNode } from "./types";

/** Title font in px per px of card height, clamped to a readable range. */
const TITLE_FONT_RATIO = 0.14;
const TITLE_FONT_MIN_PX = 11;
const TITLE_FONT_MAX_PX = 17;
/** Card padding as a fraction of card width, capped in px. */
const PAD_RATIO = 0.06;
const PAD_MAX_PX = 12;
/**
 * Average advance of a 600-weight sans glyph in ems. A layout estimate only;
 * erring wide, since a name a hair too wide for its card is cut.
 */
const CHAR_EM = 0.64;
/** Title row overhead beside the label, in title ems: glyph + gap, role dot. */
const GLYPH_EMS = 1.4;
const DOT_EMS = 1.15;
/** Widest card a long name may ask for (width : height). */
export const LABEL_ASPECT_MAX = 2.3;
/**
 * On-screen height of a file card when the folder holding it is framed (a
 * dive), on a 1600x1000 window. That is below the height where the title font
 * stops shrinking, so a file's name is sized at the floor font there.
 */
const FILE_FRAMED_PX = 44;
/** A small file card's padding and role-dot reserve beside its name, in px (no glyph; see `card-text.ts`). */
const FILE_TITLE_OVERHEAD_PX = 26;

/** Progressive disclosure by on-screen size. */
export type CardTier = "hidden" | "tiny" | "small" | "medium" | "large";

/** Minimum on-screen [width, height] per tier, largest first. */
const TIER_MIN: ReadonlyArray<readonly [CardTier, number, number]> = [
  ["large", 260, 170],
  ["medium", 120, 72],
  ["small", 96, 52],
  ["tiny", 44, 16],
];

/**
 * - tiny: name only.
 * - small: name + footer.
 * - medium: + chips and up to two summary lines.
 * - large: + a longer summary and a stats row.
 */
export function cardTier(w: number, h: number): CardTier {
  for (const [tier, minW, minH] of TIER_MIN) {
    if (w >= minW && h >= minH) return tier;
  }
  return "hidden";
}

/** Summary line cap per tier (the space left on the card may allow fewer). */
export function summaryLineCap(tier: CardTier): number {
  if (tier === "large") return 6;
  return tier === "medium" ? 2 : 0;
}

/** Title font size for a card of on-screen height `h`, in whole px (crisp). */
export function titleFontPx(h: number): number {
  return Math.round(
    Math.min(TITLE_FONT_MAX_PX, Math.max(TITLE_FONT_MIN_PX, h * TITLE_FONT_RATIO)),
  );
}

export function cardPadPx(w: number): number {
  return Math.round(Math.min(PAD_MAX_PX, w * PAD_RATIO));
}

/**
 * Files and folders are paths (break at separators, cut in the middle). A
 * folder named with spaces is a page title ("Routers Overview"), so it wraps
 * and ends like prose.
 */
export function titleMode(kind: ZoomKind, name = ""): WrapMode {
  if (kind === "file") return "path";
  return kind === "folder" && !name.includes(" ") ? "path" : "words";
}

/**
 * Characters on the longer line when `name` is split across two lines as
 * evenly as its break points allow, and never less than its longest token
 * (a word broken mid-way reads badly).
 */
export function twoLineChars(name: string, mode: WrapMode): number {
  const tokens = tokenize(name, mode);
  if (tokens.length === 0) return 0;
  const joinLen = mode === "words" ? 1 : 0;
  const total = tokens.reduce((s, t) => s + t.length, 0) + joinLen * (tokens.length - 1);
  let longest = 0;
  let best = total;
  let head = 0;
  for (let i = 0; i < tokens.length; i++) {
    const len = tokens[i]!.length;
    longest = Math.max(longest, len);
    head += len + (i > 0 ? joinLen : 0);
    const tail = total - head - (i < tokens.length - 1 ? joinLen : 0);
    best = Math.min(best, Math.max(head, tail));
  }
  return Math.max(longest, best);
}

/**
 * The width : height a card needs so its name fits in two title lines at the
 * renderer's font rule (`TITLE_FONT_RATIO` of card height). The font scales
 * with the card, so this ratio holds at any zoom where the font is unclamped:
 * cards at least ~80px tall, the overview size of a top-level card.
 *
 * A file is mostly read below that, at the floor font, when its folder is
 * framed (`FILE_FRAMED_PX`); sized by the scaled rule alone, a 20-character
 * name showed five. Its card widens for the floor font at that height too. A
 * name past `LABEL_ASPECT_MAX` relies on the ellipsis rather than stretch the
 * card into a strip.
 */
export function labelAspect(name: string, kind: ZoomKind): number {
  const labelEms = twoLineChars(name, titleMode(kind, name)) * CHAR_EM;
  const scaled = (TITLE_FONT_RATIO * (labelEms + GLYPH_EMS + DOT_EMS)) / (1 - 2 * PAD_RATIO);
  const atFloor =
    kind === "file" ? (labelEms * TITLE_FONT_MIN_PX + FILE_TITLE_OVERHEAD_PX) / FILE_FRAMED_PX : 0;
  return Math.min(LABEL_ASPECT_MAX, Math.max(scaled, atFloor));
}

/** The shared empty chip list (no allocation per frame). */
export const NO_CHIPS: readonly string[] = [];

const chipCache = new WeakMap<ZoomNode, readonly string[]>();

/**
 * Technology chips for the meta row: a subtree's top technologies, or a
 * file's language, in display form (see `techName`). Cached per node object
 * (the scene keeps nodes stable), so a frame builds no array.
 */
export function cardTechnologies(node: ZoomNode): readonly string[] {
  const hit = chipCache.get(node);
  if (hit) return hit;
  const raw = node.technologies?.length ? node.technologies : node.language ? [node.language] : [];
  const chips = raw.length > 0 ? raw.map(techName) : NO_CHIPS;
  chipCache.set(node, chips);
  return chips;
}

const badges = new WeakMap<ZoomNode, string>();

/**
 * Footer badge that replaces the kind label: the container a node narrows to
 * ("API", "Web app"). Set once per scene by `buildScene`
 * (it needs the parent and the container list), read per frame.
 */
export function cardBadge(node: ZoomNode): string | null {
  return badges.get(node) ?? null;
}

export function setCardBadge(node: ZoomNode, badge: string | null): void {
  if (badge) badges.set(node, badge);
  else badges.delete(node);
}

/**
 * The single most useful bottom-right signal. Containers show how many files
 * they hold; a file shows its language unless the chip row already does.
 */
export function primaryMetric(node: ZoomNode, chipsShown: boolean): string {
  if (node.kind === "file") return !chipsShown && node.language ? techName(node.language) : "";
  const n = node.metrics.file_count;
  return n > 0 ? `${n} ${n === 1 ? "file" : "files"}` : "";
}

function count(n: number, one: string, many: string): string | null {
  return n > 0 ? `${n} ${n === 1 ? one : many}` : null;
}

const statsCache = new WeakMap<ZoomNode, readonly string[]>();

/**
 * The stats row, shortest first: `[a, "a · b", "a · b · c"]`. A row too
 * narrow for all of it shows the longest prefix that fits, so a stat is
 * dropped whole rather than cut ("4 entry p…"). Cached per node object (the
 * scene keeps nodes stable), so a frame builds no strings.
 */
export function cardStatPrefixes(node: ZoomNode): readonly string[] {
  const hit = statsCache.get(node);
  if (hit !== undefined) return hit;
  const m = node.metrics;
  const parts =
    node.kind === "file"
      ? nodeRoles(node)
      : [
          count(m.hotspot_count, "hotspot", "hotspots"),
          count(m.entry_point_count, "entry point", "entry points"),
          count(m.dead_count, "dead file", "dead files"),
        ].filter((p): p is string => p !== null);
  const out = parts.map((_, i) => parts.slice(0, i + 1).join(" · "));
  statsCache.set(node, out);
  return out;
}

/**
 * The full stats row: role counts over a container's subtree, or a file's
 * own roles in words. "" when there are none.
 */
export function cardStats(node: ZoomNode): string {
  const all = cardStatPrefixes(node);
  return all.length > 0 ? all[all.length - 1]! : "";
}
