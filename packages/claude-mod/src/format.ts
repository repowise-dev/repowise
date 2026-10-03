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
