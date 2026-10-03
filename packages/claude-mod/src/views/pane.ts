/**
 * The `/lens` pane around the map: a row of tabs (Map, Ask, Recap), the Ask
 * field with the last reply, and the session recap. Every recap figure is
 * read from the session model as it stands; nothing is re-derived.
 */

import { fit } from "../format";
import type { PaneTab } from "../model/ask";
import { testsToRun } from "../model/review";
import { surfacedDecisions, type SessionState } from "../model/session";
import { replyMarkdown } from "./answer";
import {
  PANE_COPY,
  RECAP_COPY,
  askFailedLine,
  askingLine,
  decisionsSurfaced,
  filesTouched,
  findingsCounts,
  overlapLine,
  recapFooter,
  savingsLine,
  testsQueued,
} from "./copy";
import { box, button, input, markdown, text, type Node } from "./elements";
import { health, overlap } from "./review";

export const TABS: readonly PaneTab[] = ["map", "ask", "recap"];
/** Button keys of the tabs; register.ts maps each to its tab. */
export const TAB_PRESS: Record<PaneTab, string> = { map: "lens-tab-map", ask: "lens-tab-ask", recap: "lens-tab-recap" };
export const ASK_KEY = "lens-ask";
/** Rows the tab row takes above each tab's body. */
export const TAB_ROWS = 1;

/** `1: Map  2: Ask  3: Recap`, the shown one at full strength. */
export function tabsView(current: PaneTab): Node {
  return box(
    { key: "lens-tabs", flexDirection: "row", columnGap: 2 },
    TABS.map((tab, i) => button(TAB_PRESS[tab], String(i + 1), PANE_COPY.tabs[tab], tab !== current)),
  );
}

const dim = (line: string, columns: number): Node => text(fit(line, columns), { dimColor: true, wrap: "truncate-end" });

function askStatus(state: SessionState, columns: number): Node[] {
  const ask = state.ask;
  switch (ask.phase) {
    case "idle":
      return [dim(PANE_COPY.askIdle, columns)];
    case "asking":
      return [dim(`> ${ask.question}`, columns), dim(askingLine(ask.tool), columns)];
    case "failed":
      return [dim(`> ${ask.question}`, columns), dim(askFailedLine(ask.tool, ask.message), columns)];
    case "answered":
      return [markdown(replyMarkdown(ask.question, ask.answer))];
  }
}

/** The field (pre-filled when `Why` opened the tab) and the last reply. */
export function askView(state: SessionState, columns: number): Node {
  const field = input({
    key: ASK_KEY,
    label: PANE_COPY.askLabel,
    placeholder: PANE_COPY.askPlaceholder,
    submitLabel: PANE_COPY.askSubmit,
    autoFocus: true,
    ...(state.pane.draft === "" ? {} : { value: state.pane.draft }),
  });
  return box({ key: "lens-ask-tab", flexDirection: "column" }, [field, ...askStatus(state, columns)]);
}

/** What the trail counted: files read or edited, and whether older ones were dropped. */
export interface TrailCounts {
  count: number;
  capped: boolean;
}

function reviewRows(state: SessionState): Array<[string, string]> {
  const risk = state.lastReview;
  if (risk === null) return [[RECAP_COPY.health, RECAP_COPY.noReview]];
  const hd = risk.health_delta;
  const compared = hd !== undefined && (hd.status === "available" || hd.status === "partial");
  const tests = testsToRun(risk);
  const shared = overlap(risk);
  const branches =
    shared !== null
      ? overlapLine(shared.branches, shared.files, shared.more)
      : risk.branch_overlap === undefined
        ? RECAP_COPY.notReported
        : RECAP_COPY.noOverlap;
  return [
    [RECAP_COPY.health, health(risk).words],
    [RECAP_COPY.findings, compared ? findingsCounts(hd.resolved, hd.findings_total) : RECAP_COPY.notCompared],
    [RECAP_COPY.tests, tests === null ? RECAP_COPY.noTests : testsQueued(tests)],
    [RECAP_COPY.overlap, branches],
  ];
}

function savedRow(state: SessionState): string {
  if (state.savings !== null) return savingsLine(state.savings, Number.POSITIVE_INFINITY);
  return state.mode === "full" ? RECAP_COPY.noSavings : RECAP_COPY.savingsNeedServer;
}

/** The recap as label and value pairs, in the order drawn. */
export function recapRows(state: SessionState, reads: TrailCounts): Array<[string, string]> {
  const decisions = surfacedDecisions(state).map((d) => d.title);
  return [
    [RECAP_COPY.files, filesTouched(state.touched.length, reads)],
    ...reviewRows(state),
    [RECAP_COPY.saved, savedRow(state)],
    [RECAP_COPY.decisions, decisions.length === 0 ? RECAP_COPY.noDecisions : decisionsSurfaced(decisions)],
  ];
}

export function recapView(state: SessionState, reads: TrailCounts, columns: number): Node {
  const rows = recapRows(state, reads).map(([label, value]) =>
    box({ flexDirection: "row" }, [text(`${label}: `, { dimColor: true }), text(fit(value, Math.max(1, columns - label.length - 2)), { wrap: "truncate-end" })]),
  );
  return box({ key: "lens-recap", flexDirection: "column" }, [...rows, dim(recapFooter(state.modelAsks), columns)]);
}

/** The pane: the tabs, then the shown tab's body. */
export function paneView(tab: PaneTab, body: Node): Node {
  return box({ key: "lens-pane", flexDirection: "column" }, [tabsView(tab), body]);
}
