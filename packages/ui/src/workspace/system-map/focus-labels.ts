/**
 * Headings for the focus level's columns ("Called by · 5", "Depends on · 3"),
 * placed above each column's first card from the positions the column grid
 * gave it. Pure, no React.
 */

import type { C4LayoutPosition } from "../../c4/layout/elk-c4-layout";
import { focusMoreId, focusNodeId, type FocusLayout } from "./focus-columns";
import type { FocusColumnId } from "./level-types";

/** Room between a heading and the column's first card. */
const LABEL_GAP = 28;

export const FOCUS_COLUMN_TITLES: Record<FocusColumnId, string> = {
  callers2: "Callers of callers",
  callers: "Called by",
  dependencies: "Depends on",
  dependencies2: "Their dependencies",
};

export interface FocusColumnLabel {
  id: string;
  label: string;
  x: number;
  y: number;
}

/** React Flow data for one heading node. */
export interface ColumnLabelData {
  label: string;
  [key: string]: unknown;
}

/** One heading per column with a placed card; an empty column has none. */
export function focusColumnLabels(
  layout: FocusLayout,
  positions: ReadonlyMap<string, C4LayoutPosition>,
): FocusColumnLabel[] {
  const out: FocusColumnLabel[] = [];
  for (const c of layout.columns) {
    const ids = c.entries.map((e) => focusNodeId(c.id, e.serviceId));
    if (c.folded.length > 0) ids.push(focusMoreId(c.id));
    const placed = ids.map((id) => positions.get(id)).filter((p): p is C4LayoutPosition => p !== undefined);
    if (placed.length === 0) continue;
    const count = c.entries.length + c.folded.length;
    out.push({
      id: `label:${c.id}`,
      label: `${FOCUS_COLUMN_TITLES[c.id]} · ${count.toLocaleString()}`,
      x: Math.min(...placed.map((p) => p.x)),
      y: Math.min(...placed.map((p) => p.y)) - LABEL_GAP,
    });
  }
  return out;
}
