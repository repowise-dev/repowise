/**
 * The change review: a card shown beneath Claude's answer (plain text, so its
 * words carry the meaning) and one band row with the health word in color and
 * the buttons. Built only from what `get_change_risk` returned.
 */

import { fit } from "../format";
import { testsToRun, type ChangeRisk, type ReviewOutcome } from "../model/review";
import {
  DETAILS,
  HEALTH_NOT_REPORTED,
  NOTHING_TO_SCORE,
  REVIEW_TIMED_OUT,
  REVIEWING,
  RUN_TESTS,
  diffShape,
  directiveLines,
  findingLine,
  healthImproved,
  improvedShort,
  newFindings,
  NO_NEW_FINDINGS,
  notCompared,
  PARTLY_COMPARED,
  moreFindings,
  noNewFindings,
  overlapLine,
  overlapShort,
  partialScope,
  resolvedToo,
  reviewFailed,
  reviewScope,
  runTestsPrompt,
  testsLine,
} from "./copy";
import { box, button, text, type Node } from "./elements";

/** Button keys; register.ts maps each to what its press runs. */
export const PRESS = { tests: "lens-review-tests", details: "lens-review-details" } as const;

/** Theme keys: green, amber and red mean health and nothing else. */
type HealthColor = "success" | "warning" | "error";

interface HealthWords {
  /** The card's sentence. */
  words: string;
  /** The band's few words. */
  short: string;
  color?: HealthColor;
}

/** A result the card can describe: not a server error and not an empty diff. */
function scored(risk: ChangeRisk): boolean {
  return risk.error === undefined && risk.status !== "nothing_to_score";
}

/** Delta statuses where both sides were compared, fully or in part. */
const COMPARED = new Set(["available", "partial"]);

function health(risk: ChangeRisk): HealthWords {
  const d = risk.directive;
  const hd = risk.health_delta;
  if (d === undefined || hd === undefined) return { words: HEALTH_NOT_REPORTED, short: HEALTH_NOT_REPORTED };
  if (!COMPARED.has(hd.status)) {
    const words = notCompared(hd.explanation.replace(/\.$/, ""));
    return { words, short: notCompared("") };
  }
  if (d.status === "clear_in_analyzed_scope") {
    if (hd.resolved > 0) return { words: healthImproved(hd.resolved), short: improvedShort(hd.resolved), color: "success" };
    return { words: noNewFindings(hd.scope?.analyzed ?? 0), short: NO_NEW_FINDINGS };
  }
  const headline = d.headline.replace(/\.$/, "");
  const words = hd.resolved > 0 ? `${headline}; ${resolvedToo(hd.resolved)}` : headline;
  if (d.status === "review_required") return { words, short: newFindings(hd.findings_total, true), color: "error" };
  if (d.status === "review_recommended") return { words, short: newFindings(hd.findings_total, false), color: "warning" };
  return { words, short: PARTLY_COMPARED };
}

function overlap(risk: ChangeRisk): { branches: string[]; files: string[]; more: boolean } | null {
  const block = risk.branch_overlap;
  const branches = block?.branches ?? [];
  if (branches.length === 0) return null;
  const files = [...new Set(branches.flatMap((b) => (b.files ?? []).map((f) => f.file)))];
  return { branches: branches.map((b) => b.branch), files, more: block?.truncated === true };
}

function isClear(risk: ChangeRisk): boolean {
  return risk.directive?.status === "clear_in_analyzed_scope";
}

function cardLines(risk: ChangeRisk): string[] {
  const hd = risk.health_delta;
  const ref = risk.ref ?? "working tree";
  const workingTree = risk.working_tree !== false;
  const h = health(risk);
  const shared = overlap(risk);
  // Nothing to act on and nobody else in these files: one line.
  if (isClear(risk) && shared === null) {
    return [`Change review · ${reviewScope(ref, workingTree, null)} · health: ${h.words}`];
  }
  const lines = [`Change review · ${reviewScope(ref, workingTree, hd?.scope?.changed ?? null)}`, `Health: ${h.words}`];
  for (const f of hd?.top_findings ?? []) lines.push(findingLine(f));
  const more = (hd?.findings_total ?? 0) - (hd?.top_findings.length ?? 0);
  if (more > 0) lines.push(moreFindings(more));
  if (hd?.status === "partial" && hd.scope !== undefined) {
    lines.push(partialScope(hd.scope.analyzed, hd.scope.changed, hd.skipped?.by_reason ?? {}));
  }
  lines.push(diffShape(risk.risk_percentile));
  const tests = testsToRun(risk);
  if (tests !== null) lines.push(testsLine(tests));
  if (shared !== null) lines.push(`Branches: ${overlapLine(shared.branches, shared.files, shared.more)}`);
  return lines;
}

/** What shows beneath Claude's answer; null while there is nothing to say. */
export function reviewText(outcome: ReviewOutcome): string | null {
  switch (outcome.phase) {
    case "none":
    case "reviewing":
      return null;
    case "failed":
      return outcome.reason === "timeout" ? REVIEW_TIMED_OUT : reviewFailed(outcome.message);
    case "done": {
      const risk = outcome.risk;
      if (risk.error !== undefined) return reviewFailed(risk.error);
      if (risk.status === "nothing_to_score") return NOTHING_TO_SCORE;
      return cardLines(risk).join("\n");
    }
  }
}

/** `1: Run tests` and `3: Details` (2 stays free for a later action). */
function buttons(risk: ChangeRisk): Node[] {
  const out: Node[] = [];
  if (testsToRun(risk) !== null) out.push(button(PRESS.tests, "1", RUN_TESTS));
  if (risk.directive !== undefined) out.push(button(PRESS.details, "3", DETAILS));
  return out;
}

const GAP = 2;

/** The band's review row: a placeholder while the review runs, then the health word and the buttons. */
export function reviewBandRow(outcome: ReviewOutcome, columns: number): Node | null {
  if (outcome.phase === "reviewing") return text(fit(REVIEWING, columns), { dimColor: true, wrap: "truncate-end" });
  if (outcome.phase !== "done" || !scored(outcome.risk)) return null;
  const risk = outcome.risk;
  const pressable = buttons(risk);
  // A plain Button draws as `1: label`.
  const buttonCells = pressable.reduce(
    (n, b) => n + (b.type === "Button" ? b.props.label.length + 3 : 0) + GAP,
    0,
  );
  const room = Math.max(0, columns - buttonCells);
  const h = health(risk);
  const shared = overlap(risk);
  const words = fit(`review · health: ${h.short}`, room);
  const parts: Node[] = [text(words, h.color === undefined ? { dimColor: true } : { color: h.color })];
  const left = room - words.length;
  const tail = shared === null ? "" : ` · ${overlapShort(shared.branches.length)}`;
  if (tail !== "" && left >= tail.length) parts.push(text(tail, { dimColor: true }));
  return box({ key: "lens-review", flexDirection: "row", columnGap: GAP }, [
    box({ flexDirection: "row" }, parts),
    ...pressable,
  ]);
}

/** The full directive, as `Details` prints it; null when the result carries none. */
export function directiveText(outcome: ReviewOutcome): string | null {
  if (outcome.phase !== "done" || outcome.risk.directive === undefined) return null;
  return directiveLines(outcome.risk.directive).join("\n");
}

/** The prompt `Run tests` submits; null when the review names no tests. */
export function runTestsText(outcome: ReviewOutcome): string | null {
  if (outcome.phase !== "done") return null;
  const tests = testsToRun(outcome.risk);
  return tests === null ? null : runTestsPrompt(tests);
}

/**
 * What `turn.complete` returns with the card: the card beneath the answer, or
 * beneath what a hook below already put there. A result with no text passes on.
 */
export function withCard(result: unknown, answer: string, card: string): unknown {
  if (typeof result !== "object" || result === null) return result;
  const r = result as { text?: unknown };
  if (typeof r.text !== "string") return result;
  return { ...r, text: r.text === answer ? card : `${r.text}\n${card}` };
}
