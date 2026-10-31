/**
 * What a card shows at a given on-screen size, and how much room its name
 * needs. Pure, browser-free and unit-tested; `card-text.ts` paints it and
 * `layout.ts` sizes cards with the same font rule, so the pack leaves room for
 * exactly the label the renderer will draw.
 *
 * A card reads like a C4 box: a header (kind glyph + name, up to two lines,
 * and the health score in the top-right corner), a muted technology line, a
 * body (the summary, wrapped to the space left, and the health composition
 * bar), and a footer (role counts with icons, file count). Detail is disclosed
 * by size tier so a small card never crams.
 */

import { formatScore } from "@repowise-dev/types/health";

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
/** Title row overhead beside the label, in title ems: glyph + gap, corner score + gap. */
const GLYPH_EMS = 1.4;
const SCORE_EMS = 1.6;
/** Widest card a long name may ask for (width : height). */
export const LABEL_ASPECT_MAX = 2.3;
/**
 * On-screen height of a file card when the folder holding it is framed (a
 * dive), on a 1600x1000 window. That is below the height where the title font
 * stops shrinking, so a file's name is sized at the floor font there.
 */
const FILE_FRAMED_PX = 44;
/** A small file card's padding and corner-score reserve beside its name, in px (no glyph; see `card-text.ts`). */
const FILE_TITLE_OVERHEAD_PX = 33;

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
 * - tiny: name (and corner score) only.
 * - small: + composition bar and footer.
 * - medium: + technologies, up to two summary lines and a "contains" list.
 * - large: + a longer summary.
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
  const scaled = (TITLE_FONT_RATIO * (labelEms + GLYPH_EMS + SCORE_EMS)) / (1 - 2 * PAD_RATIO);
  const atFloor =
    kind === "file" ? (labelEms * TITLE_FONT_MIN_PX + FILE_TITLE_OVERHEAD_PX) / FILE_FRAMED_PX : 0;
  return Math.min(LABEL_ASPECT_MAX, Math.max(scaled, atFloor));
}

/** The shared empty list (no allocation per frame). */
export const NO_TECH: readonly string[] = [];

const techCache = new WeakMap<ZoomNode, readonly string[]>();

/**
 * The muted technology line under the title, shortest first:
 * `["Python", "Python · React", ...]`, so a narrow card drops a technology
 * whole rather than cut one. A subtree's top technologies in display form
 * (see `techName`), then its container badge ("CLI") when it has one. A file
 * has none: its extension says the language. Cached per node object (the
 * scene keeps nodes stable), so a frame builds no strings.
 */
export function cardTechPrefixes(node: ZoomNode): readonly string[] {
  if (node.kind === "file") return NO_TECH;
  const hit = techCache.get(node);
  if (hit) return hit;
  const parts = (node.technologies ?? []).map(techName);
  const badge = cardBadge(node);
  if (badge) parts.push(badge);
  const out = parts.length > 0 ? prefixes(parts) : NO_TECH;
  techCache.set(node, out);
  return out;
}

function prefixes(parts: readonly string[]): string[] {
  return parts.map((_, i) => parts.slice(0, i + 1).join(" · "));
}

const badges = new WeakMap<ZoomNode, string>();

/**
 * The container a node narrows to ("API", "CLI"), shown at the end of its
 * technology line. Set once per scene by `buildScene` (it needs the parent and
 * the container list), read per frame.
 */
export function cardBadge(node: ZoomNode): string | null {
  return badges.get(node) ?? null;
}

export function setCardBadge(node: ZoomNode, badge: string | null): void {
  if (badge) badges.set(node, badge);
  else badges.delete(node);
  techCache.delete(node);
}

/** Bottom-right of a container's footer: how many files it holds. "" for a file. */
export function primaryMetric(node: ZoomNode): string {
  if (node.kind === "file") return "";
  const n = node.metrics.file_count;
  return n > 0 ? `${n} ${n === 1 ? "file" : "files"}` : "";
}

/** A role the card marks with an icon. On-flow is not one: nearly every file is on a flow. */
export type CardRole = "hotspot" | "entry" | "dead";

export interface CardRoleMark {
  role: CardRole;
  /** The subtree count as text ("390"); "" on a file, whose icon alone says it. */
  count: string;
}

const NO_ROLES: readonly CardRoleMark[] = [];
const roleCache = new WeakMap<ZoomNode, readonly CardRoleMark[]>();

/**
 * The roles a card marks, hotspot then entry point then dead code, only where
 * true: a container's non-zero subtree counts (its footer), or a file's own
 * flags (icons beside its name). Cached per node object, so a frame builds no
 * strings.
 */
export function cardRoleMarks(node: ZoomNode): readonly CardRoleMark[] {
  const hit = roleCache.get(node);
  if (hit) return hit;
  const m = node.metrics;
  const file = node.kind === "file";
  const out: CardRoleMark[] = [];
  const add = (role: CardRole, flag: boolean, n: number): void => {
    if (file ? flag : n > 0) out.push({ role, count: file ? "" : String(n) });
  };
  add("hotspot", node.is_hotspot, m.hotspot_count);
  add("entry", node.is_entry_point, m.entry_point_count);
  add("dead", node.is_dead, m.dead_count);
  const marks = out.length > 0 ? out : NO_ROLES;
  roleCache.set(node, marks);
  return marks;
}

const scoreLabels = new WeakMap<ZoomNode, string>();

/**
 * The corner health score as text ("6.8", rounded down like every other
 * surface; see `formatScore`), or null when the node is unscored. Cached per
 * node object, so a frame builds no strings.
 */
export function cardScore(node: ZoomNode): string | null {
  if (node.health_score === null) return null;
  let s = scoreLabels.get(node);
  if (!s) scoreLabels.set(node, (s = formatScore(node.health_score)));
  return s;
}
