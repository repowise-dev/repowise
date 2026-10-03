/**
 * The `/lens` pane: a row of tabs (Flow, Map, Recap), the Ask field (off the
 * bar) with the last reply, and the session recap. Every recap figure is
 * read from the session model as it stands; nothing is re-derived.
 */

import { fit } from "../format";
import { TAB_BAR, type BarTab, type PaneTab } from "../model/ask";
import { testsToRun, type ChangeRisk } from "../model/review";
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
  testsToRunCount,
} from "./copy";
import { box, button, input, markdown, text, type Node } from "./elements";
import { health, overlap } from "./review";

/** The tab bar as shown: Flow leaves it when its toggle is off. */
export function tabBar(flowOn: boolean): readonly BarTab[] {
  return flowOn ? TAB_BAR : TAB_BAR.filter((t) => t !== "flow");
}
/** A tab's Button key; register.ts maps each to its tab. */
export const tabPress = (tab: BarTab): string => `lens-tab-${tab}`;
export const ASK_KEY = "lens-ask";
/** Rows the tab row takes above each tab's body. */
export const TAB_ROWS = 1;

/** `1: Flow  2: Map  3: Recap`, the shown one at full strength (none while the Ask field shows). */
export function tabsView(current: PaneTab, tabs: readonly BarTab[] = TAB_BAR): Node {
  return box(
    { key: "lens-tabs", flexDirection: "row", columnGap: 2 },
    tabs.map((tab, i) => button(tabPress(tab), String(i + 1), PANE_COPY.tabs[tab], tab !== current)),
  );
}

const dim = (line: string, columns: number): Node => text(fit(line, columns), { dimColor: true, wrap: "truncate-end" });

function askStatus(state: SessionState, columns: number): Node[] {
  const ask = state.ask;
  switch (ask.phase) {
    case "idle":
      return [dim(PANE_COPY.askIdle, columns)];
    case "asking":
      return [
        dim(`> ${ask.question}`, columns),
        dim(askingLine(ask.tool), columns),
        ...(ask.busy ? [dim(PANE_COPY.askBusy, columns)] : []),
      ];
    case "failed":
      return [dim(`> ${ask.question}`, columns), dim(askFailedLine(ask.message), columns)];
    case "answered":
      return [markdown(replyMarkdown(ask.question, ask.answer))];
  }
}

/**
 * The field (pre-filled when `Why` opened the tab) and the last reply. Once a
 * question is asked no value is drawn, so the field keeps what was typed and
 * a follow-up can edit the last question.
 */
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

/** A label and its value, or a dim sub-head over the rows that follow. */
export type RecapRow = [label: string, value: string] | string;

function overlapWords(risk: ChangeRisk): string {
  const shared = overlap(risk);
  if (shared !== null) return overlapLine(shared.branches, shared.files, shared.more);
  return risk.branch_overlap === undefined ? RECAP_COPY.notReported : RECAP_COPY.noOverlap;
}

function reviewRows(state: SessionState): RecapRow[] {
  const risk = state.lastReview;
  if (risk === null) return [[RECAP_COPY.review, RECAP_COPY.noReview]];
  const hd = risk.health_delta;
  const compared = hd !== undefined && (hd.status === "available" || hd.status === "partial");
  const tests = testsToRun(risk);
  return [
    RECAP_COPY.fromReview,
    [RECAP_COPY.health, health(risk).words],
    [RECAP_COPY.findings, compared ? findingsCounts(hd.resolved, hd.findings_total) : RECAP_COPY.notCompared],
    [RECAP_COPY.tests, tests === null ? RECAP_COPY.noTests : testsToRunCount(tests)],
    [RECAP_COPY.overlap, overlapWords(risk)],
  ];
}

function savedRow(state: SessionState): string {
  if (state.savings !== null) return savingsLine(state.savings, Number.POSITIVE_INFINITY);
  return state.mode === "full" ? RECAP_COPY.noSavings : RECAP_COPY.savingsNeedServer;
}

/** The recap in the order drawn. */
export function recapRows(state: SessionState, touched: TrailCounts): RecapRow[] {
  const decisions = surfacedDecisions(state).map((d) => d.title);
  return [
    [RECAP_COPY.files, filesTouched(state.touched.length, touched)],
    ...reviewRows(state),
    [RECAP_COPY.saved, savedRow(state)],
    [RECAP_COPY.decisions, decisions.length === 0 ? RECAP_COPY.noDecisions : decisionsSurfaced(decisions)],
  ];
}

/** Labels in a dim column wide enough for the longest, values beside them. */
export function recapView(state: SessionState, touched: TrailCounts, columns: number): Node {
  const rows = recapRows(state, touched);
  const width = Math.max(...rows.map((r) => (typeof r === "string" ? 0 : r[0].length))) + 2;
  const drawn = rows.map((r) =>
    typeof r === "string"
      ? dim(r, columns)
      : box({ flexDirection: "row" }, [
          text(r[0].padEnd(width), { dimColor: true }),
          text(fit(r[1], Math.max(1, columns - width)), { wrap: "truncate-end" }),
        ]),
  );
  return box({ key: "lens-recap", flexDirection: "column" }, [...drawn, dim(recapFooter(state.modelAsks), columns)]);
}

/** The pane: the tabs, then the shown tab's body. */
export function paneView(tab: PaneTab, body: Node, tabs: readonly BarTab[] = TAB_BAR): Node {
  return box({ key: "lens-pane", flexDirection: "column" }, [tabsView(tab, tabs), body]);
}
