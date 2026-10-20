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
  // Rows with buttons keep their place within the cap: the review's (never
  // hidden) and the brief's, which takes hotkey 4 beside the review's 1 to 3.
  const review = reviewBandRow(state.review.outcome, viewport.columns, state.review.decision);
  const actions = [review, briefBandRow(state, viewport.columns, review !== null)].filter((n): n is Node => n !== null);
  const own = [...bandRows(state, viewport.columns), ...extra].slice(0, Math.max(0, MAX_BAND_ROWS - actions.length));
  if (own.length === 0 && actions.length === 0) return null;
  const nodes = own.map((row) => text(fit(row, viewport.columns), { dimColor: true, wrap: "truncate-end" }));
  return box({ key: "lens-band", flexDirection: "column" }, [...nodes, ...actions]);
}
