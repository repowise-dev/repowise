import { formatNumber } from "@repowise-dev/ui/lib/format";

/** `1 file`, `1,204 files`. */
export function countOf(n: number, singular: string, plural: string): string {
  return `${formatNumber(n)} ${n === 1 ? singular : plural}`;
}

/**
 * Cuts `text` to `columns` cells, ending in an ellipsis when cut. Counts UTF-16
 * units, which matches cells for the BMP text Lens writes.
 */
export function fit(text: string, columns: number): string {
  if (columns <= 0) return "";
  if (text.length <= columns) return text;
  return `${text.slice(0, columns - 1).trimEnd()}…`;
}

/** Code points that are not one terminal cell: controls, lone surrogates, East Asian wide and fullwidth. */
const NOT_ONE_CELL = /[\u0000-\u001f\u007f-\u009f\ud800-\udfffᄀ-ᅟ⺀-꓏가-힣豈-﫿︰-﹏＀-｠￠-￦]/;

/**
 * `text` with every code point that is not one terminal cell (outside the
 * BMP, a lone surrogate, East Asian wide, a control) replaced by `?`, so its
 * length is its width in cells.
 */
export function cellSafe(text: string): string {
  // A code point outside the BMP comes out of Array.from as two code units.
  return Array.from(text, (ch) => (ch.length === 1 && !NOT_ONE_CELL.test(ch) ? ch : "?")).join("");
}
