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
  helperSite,
  moveTarget,
  splitGroups,
  splitResidual,
  splitShimRequired,
} from "../../refactoring/types";

export function planSourceLink(path: string, start: number | null, end: number | null): string {
  if (start && end) return `\`${path}:${start}-${end}\``;
  if (start) return `\`${path}:${start}\``;
  return `\`${path}\``;
}

/** Render the concrete, type-specific steps the agent should carry out. The
 *  detection is already done deterministically; this is the executable plan. */
export function refactoringPlanSteps(plan: RefactoringPlan): string {
  switch (plan.refactoring_type) {
    case "extract_class": {
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
    case "extract_helper": {
      const occ = extractHelperOccurrences(plan);
      const site = helperSite(plan);
      const lines = occ.map((o) => `- ${planSourceLink(o.file, o.line_start, o.line_end)}`);
      return [
        `Extract the duplicated block (${occ.length} occurrence${
          occ.length === 1 ? "" : "s"
        }) into one shared helper${site ? ` near \`${site}\`` : ""}:`,
        "",
        lines.join("\n"),
        "",
        "Define the helper once, replace every occurrence with a call to it, and confirm the behavior is identical at each site (watch for small per-site differences that need a parameter).",
      ].join("\n");
    }
    case "extract_method": {
      const em = extractMethodPlan(plan);
      if (!em.span) return "Extract the indicated slice into a helper method.";
      const params = em.params.length ? em.params.join(", ") : "(none)";
      const returns = em.returns.length ? em.returns.join(", ") : "(nothing)";
      const name = em.suggested_name ?? "a clearly named helper";
      return [
        `Extract lines ${em.span.start}–${em.span.end} of \`${plan.target_symbol}\` into ${name}:`,
        "",
        `- **Parameters (in):** ${params}`,
        `- **Returns (out):** ${returns}`,
        "",
        "Move exactly those lines into the new helper in the same scope, pass the parameters above, return the value(s) above, and replace the original lines with a single call to it. Preserve behavior exactly: change nothing outside the span and that one call site.",
      ].join("\n");
    }
    case "move_method": {
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
    case "break_cycle": {
      const members = cycleMembers(plan);
      const edges = cutEdges(plan);
      const edgeLines = edges.map((e) => `- \`${e.from}\` → \`${e.to}\``);
      return [
        `Break the import cycle across ${members.length} file${
          members.length === 1 ? "" : "s"
        } by cutting ${edges.length} edge${edges.length === 1 ? "" : "s"}:`,
        "",
        edgeLines.join("\n"),
        "",
        "For each edge, invert the dependency or introduce an abstraction/interface so the importer no longer needs the importee at module load time. Don't just move the import inside a function unless that genuinely breaks the cycle.",
      ].join("\n");
    }
    case "split_file": {
      const groups = splitGroups(plan).filter((g) => g.symbols.length > 0);
      const residual = splitResidual(plan);
      const shim = splitShimRequired(plan);
      const lines = groups.map(
        (g, i) => `- **${g.suggested_file ?? `part${i + 1}`}** — ${g.symbols.join(", ")}`,
      );
      return [
        `Split \`${plan.file_path}\` into ${groups.length} file${
          groups.length === 1 ? "" : "s"
        } along the seams below. Move each group's symbols together:`,
        "",
        lines.join("\n"),
        residual.length
          ? `\nLeave in the original file (shared by more than one group): ${residual
              .map((symbol) => `\`${symbol}\``)
              .join(", ")}.`
          : "",
        "",
        shim
          ? "Other files import this module, so keep the original path importable: re-export the moved symbols from it, or update every importer. Do not break the existing import surface."
          : "Update every importer of the moved symbols.",
        "The suggested filenames are a starting point, not a requirement; rename them if the repo has a clearer convention.",
      ]
        .filter((line) => line !== "")
        .join("\n");
    }
    default:
      return "Apply the refactoring described above.";
  }
}
