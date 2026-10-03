/**
 * The band above the prompt. Empty at rest; at most two rows, each an
 * exception or a one-time hint; gives way to a survey.
 */

import { fit } from "../format";
import type { SessionState } from "../model/session";
import { HINTS, freshnessLine, savingsLine } from "./copy";
import { box, text, type Node } from "./elements";

export const MAX_BAND_ROWS = 2;

export interface BandViewport {
  columns: number;
  hasSurvey: boolean;
}

export function bandRows(state: SessionState, columns = Number.POSITIVE_INFINITY): string[] {
  const rows: string[] = [];
  if (state.hint !== null) rows.push(HINTS[state.hint]);
  if (state.freshness !== null) rows.push(freshnessLine(state.freshness));
  if (state.savings !== null) rows.push(savingsLine(state.savings, columns));
  return rows.slice(0, MAX_BAND_ROWS);
}

/** null means draw nothing: pass the site on. */
export function bandView(state: SessionState, viewport: BandViewport): Node | null {
  if (viewport.hasSurvey) return null;
  const rows = bandRows(state, viewport.columns);
  if (rows.length === 0) return null;
  return box(
    { key: "lens-band", flexDirection: "column" },
    rows.map((row) => text(fit(row, viewport.columns), { dimColor: true, wrap: "truncate-end" })),
  );
}
