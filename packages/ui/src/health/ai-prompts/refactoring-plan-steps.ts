/**
 * The concrete, type-specific steps of one refactoring plan, shared by the
 * single-plan prompt and the opportunity prompt that embeds each of its plans.
 */

import type { RefactoringPlan } from "@repowise-dev/types/refactoring";

import {
  cutEdges,
  cycleMembers,
  extractClassGroups,
  extractHelperOccurrences,
  extractMethodPlan,
  HELPER_NAME_PLACEHOLDER,
  helperSite,
  moveTarget,
  splitGroups,
  splitResidual,
  splitShimRequired,
} from "../../refactoring/types";
import { pluralS } from "./shared";

export function planSourceLink(path: string, start: number | null, end: number | null): string {
  if (start && end) return `\`${path}:${start}-${end}\``;
  if (start) return `\`${path}:${start}\``;
  return `\`${path}\``;
}

function extractClassSteps(plan: RefactoringPlan): string {
  const groups = extractClassGroups(plan).filter(
    (g) => g.methods.length > 0 || g.fields.length > 0,
  );
  const lines = groups.map((g, i) => {
    const name = g.name ?? `NewClass${i + 1}`;
    const methods = g.methods.length ? g.methods.join(", ") : "(none)";
    const fields = g.fields.length ? g.fields.join(", ") : "(none)";
    return `- **${name}** — methods: ${methods}; fields: ${fields}`;
  });
  return [
    `Split \`${plan.target_symbol}\` into ${groups.length} cohesive class${
      groups.length === 1 ? "" : "es"
    }, one per group below. Each group's methods and the fields they touch move together:`,
    "",
    lines.join("\n"),
    "",
    "Pick a clear name for each group (the `NewClass*` placeholders are not final), keep the original class as a thin facade or update call sites, and preserve behavior.",
  ].join("\n");
}

function extractHelperSteps(plan: RefactoringPlan): string {
  const occ = extractHelperOccurrences(plan);
  const site = helperSite(plan);
  const lines = occ.map((o) => `- ${planSourceLink(o.file, o.line_start, o.line_end)}`);
  return [
    `Extract the duplicated block (${occ.length} occurrence${pluralS(
      occ.length,
    )}) into one shared helper${site ? ` near \`${site}\`` : ""}:`,
    "",
    lines.join("\n"),
    "",
    "Define the helper once, replace every occurrence with a call to it, and confirm the behavior is identical at each site (watch for small per-site differences that need a parameter).",
  ].join("\n");
}

/** How an Extract Method edit is made. Core's recipe says the same words
 *  (`recipe.SCRIPT_RULE`, `LINE_RULE`, `ELSEWHERE_NOTE`; a parity test holds them). */
export const SCRIPT_RULE_TEXT =
  "Do not generate a script that rewrites the file. Make each edit directly in the file, one at a time, re-reading the span after each.";
export const LINE_RULE_TEXT =
  "Do not find a span by its line numbers. They are where it was when planned and move with every edit; match its first and last lines.";
const ELSEWHERE_NOTE = "Text repeats in the file: edit only inside this function.";

interface SpanAnchors {
  first: string;
  last: string;
  /** "function": the text also occurs elsewhere in the file. */
  unique_in: "file" | "function";
}

/** The span's first and last lines as core serves them (`plan.anchors`), or
 *  null on a plan stored before them. Never computed here. */
function spanAnchors(plan: RefactoringPlan): SpanAnchors | null {
  const raw = ((plan.plan ?? {}) as Record<string, unknown>).anchors as
    | Record<string, unknown>
    | null
    | undefined;
  if (!raw || typeof raw.first !== "string" || typeof raw.last !== "string") return null;
  return {
    first: raw.first,
    last: raw.last,
    unique_in: raw.unique_in === "function" ? "function" : "file",
  };
}

/** *text* fenced at column 0, so its indentation reads exactly as the file has it. */
function fenced(text: string): string[] {
  const ticks = Math.max(0, ...(text.match(/`+/g) ?? []).map((run) => run.length));
  const fence = "`".repeat(Math.max(3, ticks + 1));
  return [fence, text, fence];
}

function extractMethodSteps(plan: RefactoringPlan): string {
  const em = extractMethodPlan(plan);
  if (!em.span) return "Extract the indicated slice into a helper method.";
  const params = em.params.length ? em.params.join(", ") : "(none)";
  const returns = em.returns.length ? em.returns.join(", ") : "(nothing)";
  const name = em.suggested_name ?? HELPER_NAME_PLACEHOLDER;
  const anchors = spanAnchors(plan);
  const lines = `${em.span.start}–${em.span.end}`;
  const rendered = [
    ...(anchors
      ? [
          "- **The span starts with:**",
          ...fenced(anchors.first),
          "- **The span ends with:**",
          ...fenced(anchors.last),
          ...(anchors.unique_in === "function" ? [`- ${ELSEWHERE_NOTE}`] : []),
        ]
      : []),
    ...(em.signature_text ? [`- **New helper:** \`${em.signature_text}\``] : []),
    ...(em.return_text ? [`- **End the helper with:** \`${em.return_text}\``] : []),
    ...(em.call_site
      ? [`- **Replace ${anchors ? "the span" : `lines ${lines}`} with:** \`${em.call_site.new_text}\``]
      : []),
    ...em.notes.map((note) => `- **Note:** ${note}`),
  ];
  const what = anchors
    ? `the span of \`${plan.target_symbol}\` that starts and ends with the lines shown (lines ${lines} when planned)`
    : `lines ${lines} of \`${plan.target_symbol}\``;
  return [
    `Extract ${what} into \`${name}\`${em.suggested_name ? "" : " (name it for what the lines do)"}:`,
    "",
    ...rendered,
    `- **Parameters (in):** ${params}`,
    `- **Returns (out):** ${returns}`,
    ...(em.needs_async ? ["- **Async:** the span awaits, so declare the helper async and await its call"] : []),
    ...(em.kind === "method"
      ? [
          `- **Method:** the span ${em.uses_receiver ? "uses" : "may use"} its object, so make the helper a method of the same object`,
        ]
      : []),
    "",
    "Move exactly those lines into the new helper in the same scope, pass the parameters above, return the value(s) above, and replace the original lines with a single call to it. Preserve behavior exactly: change nothing outside the span and that one call site.",
    anchors ? `${SCRIPT_RULE_TEXT} ${LINE_RULE_TEXT}` : SCRIPT_RULE_TEXT,
  ].join("\n");
}

function moveMethodSteps(plan: RefactoringPlan): string {
  const mv = moveTarget(plan);
  if (!mv) return "Move the method to the class it belongs to.";
  return [
    `Move \`${mv.method}\` from \`${mv.from_class}\` to \`${mv.to_class}\`${
      mv.to_file ? ` (in \`${mv.to_file}\`)` : ""
    }.`,
    "",
    "The method uses the target class's data more than its own. Move it, update both classes, and fix every call site. Only do this if the target class is legally accessible from the call sites.",
  ].join("\n");
}

function breakCycleSteps(plan: RefactoringPlan): string {
  const members = cycleMembers(plan);
  const edges = cutEdges(plan);
  const edgeLines = edges.map((e) => `- \`${e.from}\` → \`${e.to}\``);
  return [
    `Break the import cycle across ${members.length} file${pluralS(
      members.length,
    )} by cutting ${edges.length} edge${pluralS(edges.length)}:`,
    "",
    edgeLines.join("\n"),
    "",
    "For each edge, invert the dependency or introduce an abstraction/interface so the importer no longer needs the importee at module load time. Don't just move the import inside a function unless that genuinely breaks the cycle.",
  ].join("\n");
}

function splitFileSteps(plan: RefactoringPlan): string {
  const groups = splitGroups(plan).filter((g) => g.symbols.length > 0);
  const residual = splitResidual(plan);
  const lines = groups.map(
    (g, i) => `- **${g.suggested_file ?? `part${i + 1}`}** — ${g.symbols.join(", ")}`,
  );
  return [
    `Split \`${plan.file_path}\` into ${groups.length} file${pluralS(
      groups.length,
    )} along the seams below. Move each group's symbols together:`,
    "",
    lines.join("\n"),
    residual.length
      ? `\nLeave in the original file (shared by more than one group): ${residual
          .map((symbol) => `\`${symbol}\``)
          .join(", ")}.`
      : "",
    "",
    splitShimRequired(plan)
      ? "Other files import this module, so keep the original path importable: re-export the moved symbols from it, or update every importer. Do not break the existing import surface."
      : "Update every importer of the moved symbols.",
    "The suggested filenames are a starting point, not a requirement; rename them if the repo has a clearer convention.",
  ]
    .filter((line) => line !== "")
    .join("\n");
}

/** One renderer per refactoring type. A Map, so no type hits a prototype key. */
const PLAN_STEPS = new Map<string, (plan: RefactoringPlan) => string>([
  ["extract_class", extractClassSteps],
  ["extract_helper", extractHelperSteps],
  ["extract_method", extractMethodSteps],
  ["move_method", moveMethodSteps],
  ["break_cycle", breakCycleSteps],
  ["split_file", splitFileSteps],
]);

/** Render the concrete, type-specific steps the agent should carry out. The
 *  detection is already done deterministically; this is the executable plan. */
export function refactoringPlanSteps(plan: RefactoringPlan): string {
  const render = PLAN_STEPS.get(plan.refactoring_type);
  return render ? render(plan) : "Apply the refactoring described above.";
}

/** What a missing test should exercise: the changed symbol, or the file for a
 *  plan that changes a whole file (a split, a cycle). Stored ids lose their path. */
export function verifySubject(plan: RefactoringPlan): string {
  if (plan.refactoring_type === "split_file" || plan.refactoring_type === "break_cycle") {
    return plan.file_path;
  }
  return plan.target_symbol.split("::").pop() || plan.file_path;
}
