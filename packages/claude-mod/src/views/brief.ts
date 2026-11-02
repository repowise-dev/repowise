/**
 * The brief offered after a compaction: files edited, decisions in play and
 * the last review's open items, as one visible prompt under BRIEF_CHARS. It
 * reaches Claude only when the person presses `Brief Claude`.
 */

import { fit } from "../format";
import { testsToRun } from "../model/review";
import { surfacedDecisions, type SessionState } from "../model/session";
import { BRIEF_CHARS, BRIEF_COPY, briefDecision, briefTests, findingLine, overlapLine } from "./copy";
import { box, button, text, type Node } from "./elements";
import { overlap } from "./review";

export const BRIEF_PRESS = "lens-brief";
/** Characters of one listed item, so one long title cannot take the whole brief. */
const ITEM_CHARS = 240;

/**
 * `head item; item; and N more`, with as many items as fit in `room`. The
 * last resort cuts the line itself, so the result never passes `room`.
 */
export function listWithin(head: string, items: readonly string[], room: number): string {
  // One line per section: a title or a finding written over several lines is joined.
  const cut = items.map((i) => fit(i.replace(/\s+/g, " ").trim(), ITEM_CHARS));
  for (let k = cut.length; k >= 0; k--) {
    const more = cut.length - k;
    const shown = cut.slice(0, k).join("; ");
    const tail = more === 0 ? "" : `${shown === "" ? "" : "; "}and ${more} more`;
    const line = `${head} ${shown}${tail}`;
    if (line.length <= room) return line;
  }
  return fit(`${head} and ${cut.length} more`, room);
}

function reviewItems(state: SessionState): string[] {
  const risk = state.lastReview;
  if (risk === null) return [];
  const findings = (risk.health_delta?.findings_total ?? 0) > 0 ? (risk.health_delta?.top_findings ?? []).map(findingLine) : [];
  const tests = testsToRun(risk);
  const shared = overlap(risk);
  return [
    ...findings,
    ...(tests === null ? [] : [briefTests(tests)]),
    ...(shared === null ? [] : [overlapLine(shared.branches, shared.files, shared.more)]),
  ];
}

/** The brief, or null when the session has nothing to hand over. */
export function briefText(state: SessionState): string | null {
  const sections: Array<[string, string[]]> = [
    [BRIEF_COPY.files, [...state.touched]],
    [BRIEF_COPY.decisions, surfacedDecisions(state).map((d) => briefDecision(d.title, d.reviewed))],
    [BRIEF_COPY.review, reviewItems(state)],
  ];
  const filled = sections.filter(([, items]) => items.length > 0);
  if (filled.length === 0) return null;
  let brief = BRIEF_COPY.intro;
  filled.forEach(([head, items], i) => {
    // An even share of what is left; a section that needs less leaves the rest to the next.
    const room = Math.floor((BRIEF_CHARS - brief.length) / (filled.length - i)) - 1;
    brief += `\n${listWithin(head, items, Math.max(0, room))}`;
  });
  return brief;
}

/**
 * `context compacted  1: Brief Claude`, while the offer stands and there is
 * something to brief. Beside a review row (hotkeys 1 to 3) it takes 4.
 */
export function briefBandRow(state: SessionState, columns: number, besideReview = false): Node | null {
  if (!state.compacted || briefText(state) === null) return null;
  return box({ key: "lens-brief", flexDirection: "row", columnGap: 2 }, [
    text(fit(BRIEF_COPY.compacted, Math.max(1, columns - BRIEF_COPY.button.length - 5)), { dimColor: true }),
    button(BRIEF_PRESS, besideReview ? "4" : "1", BRIEF_COPY.button),
  ]);
}
