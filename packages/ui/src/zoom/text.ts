/**
 * Text layout for canvas cards: wrapping, ellipsis, and a bounded cache.
 * Pure and browser-free: every function takes a `measure` callback (a 2D
 * context in the browser, a stub in tests), so it is unit-testable without a
 * canvas.
 *
 * Cards are redrawn every frame during zoom and pan, so the renderer never
 * wraps text directly. It asks `TextLayoutCache`, which keys finished lines by
 * (font, text, width bucket, line cap, mode) and measures each character once
 * per font, so a steady frame does no wrapping and no frame calls
 * `measureText` once the characters on screen are known.
 */

/** Anything that measures a string in a settable font (a 2D context fits). */
interface TextMeasurer {
  font: string;
  measureText(text: string): { width: number };
}

/**
 * How a label breaks when it does not fit on one line.
 * - "words": prose and human names break at spaces, the last line ends in "…".
 * - "path": file and folder names break after `-`, `_`, `.`, `/` or a space,
 *   and the overflow is cut from the middle so the extension and tail survive.
 */
export type WrapMode = "words" | "path";

const ELLIPSIS = "…";
const PATH_SEPARATORS = "-_/ ";

/**
 * Split after a separator, but never before a file's extension: "health_map."
 * over "py" reads worse than "health_" over "map.py". A dot separates only when
 * another dot follows it.
 */
function splitPath(s: string): string[] {
  const out: string[] = [];
  let start = 0;
  for (let i = 1; i < s.length; i++) {
    const prev = s[i - 1]!;
    if (PATH_SEPARATORS.includes(prev) || (prev === "." && s.indexOf(".", i) !== -1)) {
      out.push(s.slice(start, i));
      start = i;
    }
  }
  out.push(s.slice(start));
  return out;
}

/**
 * Longest prefix of `text` (in chars) whose width fits `maxW`, walking one
 * character at a time. Widths are treated as additive, as in `fillLines`, so
 * this stops after the visible characters instead of re-measuring prefixes.
 */
function fitPrefix(text: string, maxW: number, measure: (s: string) => number): number {
  let w = 0;
  for (let i = 0; i < text.length; ) {
    // Step by code point so a cut never splits a surrogate pair (emoji).
    const step = isHighSurrogate(text, i) ? 2 : 1;
    w += measure(step === 1 ? text[i]! : text.slice(i, i + 2));
    if (w > maxW) return i;
    i += step;
  }
  return text.length;
}

function isHighSurrogate(text: string, i: number): boolean {
  const c = text.charCodeAt(i);
  return c >= 0xd800 && c <= 0xdbff;
}

/** `i`, moved back one if it would split a surrogate pair. */
function safeCut(text: string, i: number): number {
  return i > 0 && isHighSurrogate(text, i - 1) ? i - 1 : i;
}

/** Cut the end of `text` so it fits `maxW`, ending in "…". "" when nothing fits. */
export function endEllipsis(text: string, maxW: number, measure: (s: string) => number): string {
  if (fitPrefix(text, maxW, measure) === text.length) return text;
  const room = maxW - measure(ELLIPSIS);
  if (room < 0) return "";
  const keep = fitPrefix(text, room, measure);
  return keep > 0 ? text.slice(0, keep).trimEnd() + ELLIPSIS : "";
}

/**
 * Cut the middle of `text` so it fits `maxW`, e.g. `zoom-de…panel.tsx`. The
 * tail keeps slightly more than the head because a file name's distinguishing
 * part (suffix, extension) sits at the end.
 */
export function middleEllipsis(
  text: string,
  maxW: number,
  measure: (s: string) => number,
): string {
  if (measure(text) <= maxW) return text;
  const n = text.length;
  const at = (k: number): string => {
    const head = safeCut(text, Math.floor(k * 0.45));
    return text.slice(0, head) + ELLIPSIS + text.slice(safeCut(text, n - (k - head)));
  };
  let lo = 0;
  let hi = n - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (measure(at(mid)) <= maxW) lo = mid;
    else hi = mid - 1;
  }
  const out = at(lo);
  return measure(out) <= maxW ? out : "";
}

/** Break points of `text` for `mode`: words, or path segments keeping their separator. */
export function tokenize(text: string, mode: WrapMode): string[] {
  const trimmed = text.trim();
  if (!trimmed) return [];
  return mode === "words" ? trimmed.split(/\s+/) : splitPath(trimmed.replace(/\s+/g, " "));
}

/**
 * Greedily fill up to `maxLines` lines from `tokens`, returning the lines and
 * the text that did not fit. Line width is the sum of token widths (kerning
 * across a join is negligible), so each distinct token is measured once.
 * Filling stops at a token too wide for a line on its own: breaking a word
 * mid-way reads worse than an ellipsis, which the caller applies to the rest.
 */
function fillLines(
  tokens: readonly string[],
  joiner: string,
  maxW: number,
  maxLines: number,
  measure: (s: string) => number,
): { lines: string[]; rest: string } {
  const joinW = joiner ? measure(joiner) : 0;
  const lines: string[] = [];
  let line = "";
  let lineW = 0;
  for (let i = 0; i < tokens.length; i++) {
    const tok = tokens[i]!;
    const tokW = measure(tok);
    if (line && lineW + joinW + tokW <= maxW) {
      line += joiner + tok;
      lineW += joinW + tokW;
      continue;
    }
    if (line) lines.push(line);
    if (lines.length >= maxLines || tokW > maxW) {
      return { lines, rest: tokens.slice(i).join(joiner) };
    }
    line = tok;
    lineW = tokW;
  }
  if (line) lines.push(line);
  return { lines, rest: "" };
}

/**
 * Wrap `text` into at most `maxLines` lines no wider than `maxW`. When it does
 * not fit, the overflow is cut at the end ("words") or in the middle ("path"),
 * on a line of its own if one is left, else joined onto the last line.
 * Returns [] when nothing fits.
 */
export function wrapText(
  text: string,
  maxW: number,
  maxLines: number,
  mode: WrapMode,
  measure: (s: string) => number,
): string[] {
  if (maxLines < 1 || maxW <= 0) return [];
  const tokens = tokenize(text, mode);
  const joiner = mode === "words" ? " " : "";
  const filled = fillLines(tokens, joiner, maxW, maxLines, measure);
  // A path token keeps its trailing space ("Routers "), so trim finished lines.
  const lines = filled.lines.map((l) => l.trimEnd());
  if (!filled.rest) return lines;
  const last = lines.length < maxLines ? filled.rest : lines.pop() + joiner + filled.rest;
  const cut =
    mode === "words" ? endEllipsis(last, maxW, measure) : middleEllipsis(last, maxW, measure);
  if (cut) lines.push(cut);
  return lines;
}

/** Widths are bucketed to this many px so a slowly zooming card reuses its wrap. */
const WIDTH_BUCKET_PX = 4;
/** Cached wraps (and, separately, character widths) kept before that store is dropped. */
const TEXT_CACHE_LIMIT = 8192;

const NO_LINES: readonly string[] = [];

/**
 * Bounded cache in front of `wrapText` and `measureText`.
 *
 * Nested maps (font, then text, then a numeric shape key) so a lookup builds no
 * key string. The width is bucketed down, so a cached wrap always fits the real
 * width. When a store passes `limit` entries it is dropped: simpler than an
 * LRU, and refilling wraps is arithmetic over the cached character widths.
 */
export class TextLayoutCache {
  private wraps = new Map<string, Map<string, Map<number, readonly string[]>>>();
  /** Per-font advance of each character seen. */
  private widths = new Map<string, Map<string, number>>();
  private wrapCount = 0;
  private charCount = 0;

  constructor(private readonly limit = TEXT_CACHE_LIMIT) {}

  /**
   * Wrapped lines for `text` in `font`. On a miss the context's font is set to
   * `font` for measuring, so the caller sets its drawing font afterwards.
   */
  lines(
    ctx: TextMeasurer,
    font: string,
    text: string,
    maxW: number,
    maxLines: number,
    mode: WrapMode,
  ): readonly string[] {
    const bucket = Math.floor(maxW / WIDTH_BUCKET_PX);
    if (!text || bucket <= 0 || maxLines < 1) return NO_LINES;
    // Packed key: the line cap must stay under 32 or it would alias a bucket.
    const lineCap = Math.min(maxLines, 31);
    const shape = bucket * 64 + lineCap * 2 + (mode === "path" ? 1 : 0);
    const hit = this.wraps.get(font)?.get(text)?.get(shape);
    if (hit) return hit;
    const measure = (s: string): number => this.width(ctx, font, s);
    const out = wrapText(text, bucket * WIDTH_BUCKET_PX, lineCap, mode, measure);
    // Wraps churn during a zoom; drop only them when full, keeping the
    // character widths (a small, stable set) so a refill measures nothing.
    if (++this.wrapCount > this.limit) {
      this.wraps.clear();
      this.wrapCount = 1;
    }
    let byText = this.wraps.get(font);
    if (!byText) this.wraps.set(font, (byText = new Map()));
    let byShape = byText.get(text);
    if (!byShape) byText.set(text, (byShape = new Map()));
    byShape.set(shape, out);
    return out;
  }

  /**
   * Width of `s` in `font`: the sum of each character's cached advance, so
   * any string (a token, an ellipsis candidate) costs no `measureText` once its
   * characters are known. Summing skips kerning, which in UI sans fonts mostly
   * tightens pairs, so the sum errs wide and wrapped text still fits.
   */
  width(ctx: TextMeasurer, font: string, s: string): number {
    let w = 0;
    for (const ch of s) w += this.charWidth(ctx, font, ch);
    return w;
  }

  /** Cached advance of one character (sets `ctx.font` on a miss). */
  private charWidth(ctx: TextMeasurer, font: string, ch: string): number {
    const hit = this.widths.get(font)?.get(ch);
    if (hit !== undefined) return hit;
    ctx.font = font;
    const w = ctx.measureText(ch).width;
    if (++this.charCount > this.limit) {
      this.widths.clear();
      this.charCount = 1;
    }
    let byText = this.widths.get(font);
    if (!byText) this.widths.set(font, (byText = new Map()));
    byText.set(ch, w);
    return w;
  }

  clear(): void {
    this.wraps.clear();
    this.widths.clear();
    this.wrapCount = 0;
    this.charCount = 0;
  }
}
