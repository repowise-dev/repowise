/**
 * Margin notes under an Edit or Write row: at most two dim lines, only for
 * what the augment hook flagged on that call. Lens adds no thresholds.
 */

import type { MarginNote } from "../model/session";
import { marginLine } from "./copy";
import { box, text, type Node } from "./elements";

export const MAX_MARGIN_LINES = 2;

/** null means draw nothing: pass the row on. */
export function marginView(notes: readonly MarginNote[] | undefined): Node | null {
  if (notes === undefined || notes.length === 0) return null;
  return box(
    { key: "lens-margin", flexDirection: "column" },
    notes.slice(0, MAX_MARGIN_LINES).map((n) => text(`  ${marginLine(n)}`, { dimColor: true, wrap: "truncate-end" })),
  );
}
