/**
 * The change review: a card shown beneath Claude's answer and one band row with
 * the health word in color and the buttons. Built only from what
 * `get_change_risk` returned.
 *
 * The engine draws the text a `turn.complete` hook returns as one row (a line
 * break is stored as U+FFFD), so the card is one row of segments, plain text
 * whose words carry the meaning.
 */

import { DARK } from "@repowise-dev/ui/brand";
import { fit } from "../format";
import { emptyDiff, reviewable, testsToRun, type ChangeRisk, type ReviewOutcome } from "../model/review";
import {
  DETAILS,
  HEALTH_NOT_REPORTED,
  REVIEW_TIMED_OUT,
  REVIEWING,
  RUN_TESTS,
  WHY,
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
export const PRESS = { tests: "lens-review-tests", why: "lens-review-why", details: "lens-review-details" } as const;

/** Green, amber and red mean health and nothing else (dark tokens: terminals are dark). */
type HealthColor = typeof DARK.success | typeof DARK.warning | typeof DARK.error;

export interface HealthWords {
  /** The card's sentence. */
  words: string;
  /** The band's few words. */
  short: string;
  color?: HealthColor;
}

type Directive = NonNullable<ChangeRisk["directive"]>;
type Delta = NonNullable<ChangeRisk["health_delta"]>;

/** Delta statuses where both sides were compared, fully or in part. */
const COMPARED = new Set(["available", "partial"]);

const NOT_REPORTED: HealthWords = { words: HEALTH_NOT_REPORTED, short: HEALTH_NOT_REPORTED };

function notComparedWords(hd: Delta): HealthWords {
  return { words: notCompared(hd.explanation.replace(/\.$/, "")), short: notCompared("") };
}

function clearWords(hd: Delta): HealthWords {
  if (hd.resolved === 0) return { words: noNewFindings(hd.scope?.analyzed ?? 0), short: NO_NEW_FINDINGS };
  return { words: healthImproved(hd.resolved), short: improvedShort(hd.resolved), color: DARK.success };
}

/** New findings by the directive's verdict; an unknown verdict is a partial comparison. */
const FINDINGS_TONE: Record<string, { required: boolean; color: HealthColor } | undefined> = {
  review_required: { required: true, color: DARK.error },
  review_recommended: { required: false, color: DARK.warning },
};

function findingsWords(d: Directive, hd: Delta): HealthWords {
  const headline = d.headline.replace(/\.$/, "");
  const words = hd.resolved > 0 ? `${headline}; ${resolvedToo(hd.resolved)}` : headline;
  const tone = FINDINGS_TONE[d.status];
  if (tone === undefined) return { words, short: PARTLY_COMPARED };
  return { words, short: newFindings(hd.findings_total, tone.required), color: tone.color };
}

export function health(risk: ChangeRisk): HealthWords {
  const d = risk.directive;
  const hd = risk.health_delta;
  if (d === undefined || hd === undefined) return NOT_REPORTED;
  if (!COMPARED.has(hd.status)) return notComparedWords(hd);
  if (d.status === "clear_in_analyzed_scope") return clearWords(hd);
  return findingsWords(d, hd);
}

export function overlap(risk: ChangeRisk): { branches: string[]; files: string[]; more: boolean } | null {
  const block = risk.branch_overlap;
  const branches = block?.branches ?? [];
  if (branches.length === 0) return null;
  const files = [...new Set(branches.flatMap((b) => (b.files ?? []).map((f) => f.file)))];
  return { branches: branches.map((b) => b.branch), files, more: block?.truncated === true };
}

function isClear(risk: ChangeRisk): boolean {
  return risk.directive?.status === "clear_in_analyzed_scope";
}

function header(risk: ChangeRisk, changed: number | null): string {
  return `Change review (${reviewScope(risk.ref ?? "working tree", risk.working_tree === true, changed)})`;
}

/** Each listed finding, then a count of the ones the server left out. */
function findingSegments(hd: Delta | undefined): string[] {
  const rows = hd?.top_findings ?? [];
  const segments = rows.map(findingLine);
  const more = (hd?.findings_total ?? 0) - rows.length;
  if (more > 0) segments.push(moreFindings(more));
  return segments;
}

/** The scope line, for a partial comparison only. */
function scopeSegments(hd: Delta | undefined): string[] {
  if (hd?.status !== "partial" || hd.scope === undefined) return [];
  return [partialScope(hd.scope.analyzed, hd.scope.changed, hd.skipped?.by_reason ?? {})];
}

function testSegments(risk: ChangeRisk): string[] {
  const tests = testsToRun(risk);
  return tests === null ? [] : [testsLine(tests)];
}

function overlapSegments(risk: ChangeRisk): string[] {
  const shared = overlap(risk);
  return shared === null ? [] : [`Branches: ${overlapLine(shared.branches, shared.files, shared.more)}`];
}

function cardSegments(risk: ChangeRisk): string[] {
  // Nothing to act on and nobody else in these files: the short form.
  if (isClear(risk) && overlap(risk) === null) return [header(risk, null), `health: ${health(risk).words}`];
  const hd = risk.health_delta;
  return [
    header(risk, hd?.scope?.changed ?? null),
    `Health: ${health(risk).words}`,
    ...findingSegments(hd),
    ...scopeSegments(hd),
    diffShape(risk.risk_percentile),
    ...testSegments(risk),
    ...overlapSegments(risk),
  ];
}

function doneText(risk: ChangeRisk): string | null {
  if (risk.error !== undefined) return reviewFailed(risk.error);
  // An empty diff has nothing to say (its percentile would rank a diff of nothing).
  if (emptyDiff(risk)) return null;
  return cardSegments(risk).join(" · ");
}

/** What shows beneath Claude's answer, as one row; null while there is nothing to say. */
export function reviewText(outcome: ReviewOutcome): string | null {
  switch (outcome.phase) {
    case "none":
    case "reviewing":
      return null;
    case "failed":
      return outcome.reason === "timeout" ? REVIEW_TIMED_OUT : reviewFailed(outcome.message);
    case "done":
      return doneText(outcome.risk);
  }
}

/** `1: Run tests`, `2: Why` (only when a decision governs an edited file) and `3: Details`. */
function buttons(risk: ChangeRisk, decision: string | null): Node[] {
  const out: Node[] = [];
  if (testsToRun(risk) !== null) out.push(button(PRESS.tests, "1", RUN_TESTS));
  if (decision !== null) out.push(button(PRESS.why, "2", WHY));
  if (risk.directive !== undefined) out.push(button(PRESS.details, "3", DETAILS));
  return out;
}

const GAP = 2;

/** Cells the buttons take: a plain Button draws as `1: label`, then the gap. */
function buttonCells(pressable: Node[]): number {
  return pressable.reduce((n, b) => n + (b.type === "Button" ? b.props.label.length + 3 : 0) + GAP, 0);
}

/** A review the band has nothing to add to: clear, no tests to run, nobody else in these files, no decision. */
function quietInBand(risk: ChangeRisk, decision: string | null): boolean {
  return isClear(risk) && testsToRun(risk) === null && overlap(risk) === null && decision === null;
}

/** The health word in its color, then the overlap words when they fit in `room`. */
function summaryParts(risk: ChangeRisk, room: number): Node[] {
  const h = health(risk);
  const words = fit(`review · health: ${h.short}`, room);
  const parts = [text(words, h.color === undefined ? { dimColor: true } : { color: h.color })];
  const shared = overlap(risk);
  const tail = shared === null ? "" : ` · ${overlapShort(shared.branches.length)}`;
  if (tail !== "" && room - words.length >= tail.length) parts.push(text(tail, { dimColor: true }));
  return parts;
}

function resultRow(risk: ChangeRisk, columns: number, decision: string | null): Node | null {
  if (!reviewable(risk) || quietInBand(risk, decision)) return null;
  const pressable = buttons(risk, decision);
  const room = Math.max(0, columns - buttonCells(pressable));
  return box({ key: "lens-review", flexDirection: "row", columnGap: GAP }, [
    box({ flexDirection: "row" }, summaryParts(risk, room)),
    ...pressable,
  ]);
}

/** The band's review row: a placeholder while the review runs, then the health word and the buttons. */
export function reviewBandRow(outcome: ReviewOutcome, columns: number, decision: string | null = null): Node | null {
  if (outcome.phase === "reviewing") return text(fit(REVIEWING, columns), { dimColor: true, wrap: "truncate-end" });
  return outcome.phase === "done" ? resultRow(outcome.risk, columns, decision) : null;
}

/** The full directive, as `Details` prints it, one transcript row per line; null when the result carries none. */
export function directiveRows(outcome: ReviewOutcome): string[] | null {
  if (outcome.phase !== "done" || outcome.risk.directive === undefined) return null;
  return directiveLines(outcome.risk.directive);
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
  return { ...r, text: r.text === answer ? card : `${r.text} · ${card}` };
}
