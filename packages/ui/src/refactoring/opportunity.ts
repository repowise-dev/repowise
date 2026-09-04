/**
 * View model for the composed opportunity: the unit the board, the map and the
 * drawer all speak.
 *
 * Pure functions over the wire types, so every surface derives the same words
 * from the same fields instead of each writing its own sentence. Nothing here
 * fetches, and nothing renders.
 */

import type {
  OpportunityStatus,
  OpportunityStep,
  RefactoringOpportunity,
} from "@repowise-dev/types/refactoring";

import { STRUCTURAL_TYPES } from "./types";
import { TYPE_ORDER, typeMeta } from "./meta";

/** The four triage states, in the order a person moves through them. */
export const TRIAGE_STATUSES: { value: OpportunityStatus; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "acknowledged", label: "Acknowledged" },
  { value: "resolved", label: "Resolved" },
  { value: "false_positive", label: "False positive" },
];

export const STATUS_LABEL: Record<OpportunityStatus, string> = {
  open: "Open",
  acknowledged: "Acknowledged",
  resolved: "Resolved",
  false_positive: "False positive",
};

/**
 * Whether the file's dominant finding is what these steps address.
 *
 * Tri-state on purpose. `null` means no dominant finding was recorded to
 * compare against, which is a different claim from "no, they address something
 * else" - and on a real index the `false` answers are the honest ones, so
 * flattening the two would turn "we could not tell" into an accusation.
 */
export function addressesPrimaryLabel(value: boolean | null | undefined): string {
  if (value === true) return "Addresses the file's main problem";
  if (value === false) return "Does not address the file's main problem";
  return "No dominant problem recorded for this file";
}

export function addressesPrimaryShort(value: boolean | null | undefined): string {
  if (value === true) return "Main problem";
  if (value === false) return "Side problem";
  return "Lead unknown";
}

/** `3 steps - 2 mechanical`: the shape of the work, in the row's own words. */
export function stepSummary(opportunity: RefactoringOpportunity): string {
  const steps = `${opportunity.step_count} step${opportunity.step_count === 1 ? "" : "s"}`;
  if (opportunity.mechanical_steps === 0) return `${steps}, all judgment`;
  if (opportunity.judgment_steps === 0) return `${steps}, all mechanical`;
  return `${steps}, ${opportunity.mechanical_steps} mechanical`;
}

/** `nested_complexity` -> `nested complexity`. */
export function humanizeBiomarker(token: string): string {
  return token.replace(/_/g, " ").trim();
}

/** One sentence naming what the opportunity is, for a row's second line. */
export function opportunityLede(opportunity: RefactoringOpportunity): string {
  const meta = typeMeta(opportunity.lead_refactoring_type || "");
  const cause = opportunity.lead_biomarker
    ? humanizeBiomarker(opportunity.lead_biomarker)
    : null;
  return cause ? `${meta.label}, against ${cause}` : meta.label;
}

/**
 * A step whose file and span describe where its symbol *was*.
 *
 * `relocated_by` names an earlier step that moves the symbol to another file,
 * so an ordered list handed to anyone - a reader or an agent - has to say the
 * later step must be located again before it is applied.
 */
export function isRelocated(step: OpportunityStep): boolean {
  return Boolean(step.relocated_by);
}

export const ORDERING_NOTE =
  "Steps are in dependency-safe order. A step marked as relocated names an " +
  "earlier step that moves its symbol to another file, so its own file and " +
  "line range say where the symbol was: find it again before applying it.";

// ---------------------------------------------------------------------------
// The structural field
// ---------------------------------------------------------------------------

/** One mark: one file's structural opportunity, at that file's coordinates. */
export interface StructuralMark {
  opportunityId: string;
  filePath: string;
  leadType: string;
  stepCount: number;
  status: OpportunityStatus;
  /** Files that import this one. */
  x: number;
  /** Lines in the file. */
  y: number;
}

const STRUCTURAL = new Set<string>(STRUCTURAL_TYPES as readonly string[]);

/**
 * The structural opportunities that can be placed on the field.
 *
 * The two axes are properties of the *file* - how big it is, how much imports
 * it - and the finalizer records them onto the opportunity because it is the
 * only place on the write path that has already computed them. Reading them off
 * the row is what lets the field draw one mark per opportunity: it used to plot
 * one per plan, so a file big enough to split and also caught in a cycle
 * appeared twice and the sprawl the composed unit removed came back as ink.
 *
 * An opportunity whose figures are absent - a store written before they were
 * recorded, or a file the analyzer never measured - is dropped rather than
 * plotted at the origin. The caller counts what it dropped and says so.
 */
export function structuralMarks(opportunities: RefactoringOpportunity[]): StructuralMark[] {
  const marks: StructuralMark[] = [];
  for (const item of opportunities) {
    if (!STRUCTURAL.has(item.lead_refactoring_type)) continue;
    const x = item.dependents;
    const y = item.file_nloc;
    // Absent is not zero. A file nothing imports has a measured `dependents` of
    // 0 and belongs on the field - it is the left edge of the axis, and it is
    // often the entrypoint. Only a figure the store never recorded is dropped.
    // (A zero `file_nloc` is a different matter: an empty file has nothing to
    // split, and the log-scaled x needs a positive value anyway.)
    if (x === undefined || !y) continue;
    marks.push({
      opportunityId: item.opportunity_id,
      filePath: item.file_path,
      leadType: item.lead_refactoring_type,
      stepCount: item.step_count,
      status: item.status,
      x,
      y,
    });
  }
  return marks;
}

/**
 * Salience order for the structural types present, most common first.
 *
 * Derived from the marks in hand, never fixed per type: the four structural
 * types are wildly unequal on this repo (74 / 17 / 6 / 3 per cent) but there is
 * no reason another codebase's cycles could not outnumber its oversized files,
 * and a hardcoded hue would then paint the dominant category as the rare one.
 * `TYPE_ORDER` breaks count ties, so the assignment is stable across renders.
 */
export function salienceOrder(marks: StructuralMark[]): string[] {
  const counts = new Map<string, number>();
  for (const mark of marks) counts.set(mark.leadType, (counts.get(mark.leadType) ?? 0) + 1);
  const rank = (type: string) => {
    const at = (TYPE_ORDER as readonly string[]).indexOf(type);
    return at === -1 ? TYPE_ORDER.length : at;
  };
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || rank(a[0]) - rank(b[0]))
    .map(([type]) => type);
}

/**
 * Two hues, then neutrals - the pattern the commits area chart already uses.
 *
 * The accent/secondary pair, and deliberately not the sequential
 * `--color-ramp-*` steps: globals.css reserves the ramp for magnitude, so
 * spending it on unrelated categories would claim an order the types do not
 * have, and it would add orange rather than reduce it. Type is carried in full
 * by the mark's shape, and named in the legend and the hover card, so hue here
 * only ranks attention - which is why the tail can recede into neutrals without
 * anything becoming unreadable.
 *
 * **`--color-accent-primary`, not `--color-accent-fill`.** They are the same
 * bright orange in dark mode, but light mode deepens the former to `#A16215`
 * precisely so it reads on warm paper: measured against `--color-bg-root`,
 * `accent-fill` is 2.12:1 in light and `accent-primary` is 4.58:1, against a
 * 3.0 floor for non-text UI. The theme doc records the same correction being
 * made once already, when the community graph's orange hub had to be deepened
 * from `#F59520` to `#C0641A` for the same reason on the same kind of canvas.
 *
 * The neutral tiers do *not* clear that floor on their own (1.25 to 2.45:1),
 * and they are not asked to: every mark carries a separating stroke, so the
 * fill only has to distinguish tiers, not to be the thing that makes a mark
 * visible. That split is the galaxy's device, for the same reason.
 */
const SALIENCE_FILLS = [
  "var(--color-accent-primary)",
  "var(--color-accent-secondary)",
  "var(--color-neutral-1)",
  "var(--color-neutral-2)",
  "var(--color-neutral-3)",
];

/**
 * The outline every mark carries, whatever its fill.
 *
 * 3.55:1 against the light page and 3.84:1 against the dark one, so a mark is
 * locatable before its fill says anything. Without it a receded type in
 * `--color-neutral-3` sits at 1.25:1 and is simply not there.
 */
export const MARK_STROKE = "var(--color-text-tertiary)";

export function salienceFill(type: string, order: string[]): string {
  const at = order.indexOf(type);
  const index = at === -1 ? order.length : at;
  return SALIENCE_FILLS[Math.min(index, SALIENCE_FILLS.length - 1)]!;
}

/** True once a type has fallen past the two hues into the neutral tail. */
export function isRecededType(type: string, order: string[]): boolean {
  const at = order.indexOf(type);
  return at === -1 || at >= 2;
}
