/**
 * The band above the prompt. Empty at rest; at most two rows, each an
 * exception or a one-time hint; gives way to a survey.
 */

import { fit } from "../format";
import type { SessionState } from "../model/session";
import { HINTS, freshnessLine, savingsLine } from "./copy";
import { box, text, type Node } from "./elements";
import { briefBandRow } from "./brief";
import { reviewBandRow } from "./review";

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

/** null means draw nothing: pass the site on. `extra` rows come after the session's own. */
export function bandView(state: SessionState, viewport: BandViewport, extra: readonly string[] = []): Node | null {
  if (viewport.hasSurvey) return null;
  // One row of buttons: the brief offer after a compaction, else the review's.
  const review = briefBandRow(state, viewport.columns) ?? reviewBandRow(state.review.outcome, viewport.columns, state.review.decision);
  // The row with buttons keeps its place within the cap.
  const own = [...bandRows(state, viewport.columns), ...extra];
  const rows = own.slice(0, review === null ? MAX_BAND_ROWS : MAX_BAND_ROWS - 1);
  if (rows.length === 0 && review === null) return null;
  const nodes = rows.map((row) => text(fit(row, viewport.columns), { dimColor: true, wrap: "truncate-end" }));
  return box({ key: "lens-band", flexDirection: "column" }, review === null ? nodes : [...nodes, review]);
}
