const SEARCH_WINDOW_LINES = 20;

export interface LocatedReference {
  line: number;
  start: number;
  end: number;
}

/**
 * Searches for `raw` (then `target`) on the recorded `line` (0-based) first, then on the
 * nearest line within ±20 lines, preferring the closest match and the line above on a tie.
 * Returns null if no match is found within the window.
 */
export function locateReference(
  lines: string[],
  raw: string | null | undefined,
  target: string | null | undefined,
  line: number,
): LocatedReference | null {
  if (lines.length === 0) return null;
  const clampedLine = Math.min(Math.max(0, line), lines.length - 1);
  const needles = [raw, target].filter((n): n is string => Boolean(n && n.length > 0));
  if (needles.length === 0) return null;

  function findOnLine(lineIdx: number): LocatedReference | null {
    if (lineIdx < 0 || lineIdx >= lines.length) return null;
    const text = lines[lineIdx];
    if (text == null) return null;
    for (const needle of needles) {
      const at = text.indexOf(needle);
      if (at >= 0) {
        return { line: lineIdx, start: at, end: at + needle.length };
      }
    }
    return null;
  }

  // 1. Check recorded line (distance 0)
  const exact = findOnLine(clampedLine);
  if (exact) return exact;

  // 2. Search outward up to ±20 lines: closest match, line above on a tie
  for (let d = 1; d <= SEARCH_WINDOW_LINES; d++) {
    const above = findOnLine(clampedLine - d);
    if (above) return above;
    const below = findOnLine(clampedLine + d);
    if (below) return below;
  }

  return null;
}
