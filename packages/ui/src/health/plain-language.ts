/**
 * Findings in the words a developer uses, for the file drawer's first screen.
 *
 * The glossary names detectors ("Brain method", "Bumpy road"); those names are
 * precise and they are jargon. A reader who has never seen Repowise needs what
 * is wrong and why it matters, so each detector gets a plain title and a
 * one-line consequence here, with its category as the fallback. The detector's
 * own label stays one click away, in the expanded row.
 */

import { bandForScore, type HealthBand } from "@repowise-dev/types/health";
import { biomarkerInfo, isHistoryBiomarker, type BiomarkerCategory } from "./biomarker-glossary";

export interface PlainIssue {
  /** What is wrong, as a short noun phrase. */
  title: string;
  /** Why it matters, in one line. */
  why: string;
}

const BY_CATEGORY: Record<BiomarkerCategory, PlainIssue> = {
  structural_complexity: {
    title: "Hard-to-follow logic",
    why: "Tangled control flow is easy to break when it changes.",
  },
  size_and_complexity: {
    title: "Function is too big",
    why: "Large functions are hard to read, review and test.",
  },
  duplication: {
    title: "Duplicated code",
    why: "A fix made in one copy is easy to miss in the others.",
  },
  test_coverage: {
    title: "Untested code",
    why: "Nothing would catch a mistake here.",
  },
  test_coverage_gradient: {
    title: "Coverage drops off",
    why: "Part of the file is tested and part is not.",
  },
  test_quality: {
    title: "Weak test",
    why: "The test runs the code but checks little of what it does.",
  },
  error_handling: {
    title: "Errors caught too broadly",
    why: "A broad catch can hide a real failure.",
  },
  performance: {
    title: "Slow pattern",
    why: "Work is repeated where it can get slow as data grows.",
  },
  organizational: {
    title: "Risky change history",
    why: "Measured from git history, not the code: no edit here clears it.",
  },
  sql: {
    title: "Risky SQL",
    why: "The query is hard to reason about or can touch more rows than meant.",
  },
};

const BY_BIOMARKER: Record<string, PlainIssue> = {
  brain_method: {
    title: "Function does too much",
    why: "Many decisions in one place make every change to it risky.",
  },
  nested_complexity: {
    title: "Deeply nested logic",
    why: "Each level of nesting is more context a reader has to hold.",
  },
  bumpy_road: {
    title: "Several tangled blocks in one function",
    why: "It reads as a few functions glued together.",
  },
  complex_conditional: {
    title: "Complicated condition",
    why: "A condition with many parts is easy to get wrong.",
  },
  low_cohesion: {
    title: "Unrelated jobs in one place",
    why: "Code that changes for different reasons ends up changing together.",
  },
  god_class: {
    title: "Class does too much",
    why: "One class owns too many responsibilities to change safely.",
  },
  complex_method: {
    title: "Too many paths through one function",
    why: "Every path is another case to test.",
  },
  large_method: {
    title: "Very long function",
    why: "Long functions are hard to read and review.",
  },
  primitive_obsession: {
    title: "Too many parameters",
    why: "Calls become positional and easy to mix up.",
  },
  untested_hotspot: {
    title: "Changes often and is untested",
    why: "Frequent edits with nothing to catch a mistake.",
  },
  prior_defect: {
    title: "Fixed for bugs often",
    why: "Files with past bug fixes tend to get more.",
  },
  churn_risk: {
    title: "Changes very often",
    why: "Heavy churn is a strong predictor of new bugs.",
  },
  change_entropy: {
    title: "Scattered changes",
    why: "Edits arrive from many unrelated pieces of work.",
  },
  co_change_scatter: {
    title: "Edits ripple to other files",
    why: "Changing this file usually means changing many others.",
  },
  knowledge_loss: {
    title: "Its main authors have moved on",
    why: "Fewer people know how it works.",
  },
  developer_congestion: {
    title: "Many people edit it at once",
    why: "Parallel edits collide and get merged wrongly.",
  },
  ownership_risk: {
    title: "No clear owner",
    why: "Nobody is clearly responsible for it.",
  },
};

export function plainIssue(biomarker: string): PlainIssue {
  return BY_BIOMARKER[biomarker] ?? BY_CATEGORY[biomarkerInfo(biomarker).category] ?? {
    title: "Code health issue",
    why: "This lowers the file's score.",
  };
}

const COUNT_WORD = ["", "one", "two", "three"];

/** The clause one kind of problem contributes to the verdict sentence. */
function clause(category: BiomarkerCategory, functions: number, onlyLength: boolean): string {
  switch (category) {
    case "structural_complexity":
    case "size_and_complexity": {
      const what = onlyLength ? "long" : "complex";
      return functions > 1
        ? `${COUNT_WORD[functions] ?? functions} functions are too ${what}`
        : `one function is too ${what}`;
    }
    case "duplication":
      return "it repeats code found elsewhere";
    case "test_coverage":
    case "test_coverage_gradient":
      return "parts of it are untested";
    case "test_quality":
      return "its tests check little";
    case "error_handling":
      return "it catches errors too broadly";
    case "performance":
      return "it has slow patterns";
    case "sql":
      return "it has risky SQL";
    case "organizational":
      return "its change history is risky";
  }
}

export interface VerdictInput {
  score: number | null;
  /** The findings the drawer leads with, worst first. */
  top: readonly { biomarker_type: string; function_name: string | null }[];
  /** Whether a paired test file was found, when that is known. */
  hasTestFile?: boolean | undefined;
  /** Every scored finding is history, not code. */
  historyOnly: boolean;
  /** The findings were loaded. Without them the verdict makes no claim. */
  findingsKnown?: boolean | undefined;
  /** Open findings not leading: how many, their summed impact, and how much
   *  of that is change history. */
  rest?: { count: number; impact: number; historyImpact: number } | undefined;
}

export interface Verdict {
  /** The sentence, or null when the evidence on hand supports none. */
  text: string | null;
  /** The sentence is the all-clear, so there is nothing to hand an agent. */
  healthy: boolean;
}

/** The verdict's opening words, on the shared bands so they agree with the mark. */
const BAND_LEAD: Record<HealthBand, string> = {
  excellent: "In good shape",
  good: "In good shape",
  fair: "Needs attention",
  needs_work: "Needs work",
  at_risk: "Hard to change safely",
};

const HISTORY_VERDICT =
  "The code itself is not flagged. Its score comes from change history, which no edit here will clear.";

/** Below this a summed impact is rounding, not a deduction. */
const IMPACT_EPSILON = 0.005;

/**
 * One sentence that says how bad the file is and why, built from the
 * findings on screen and nothing else.
 *
 * At most two reasons, worst first, because a sentence listing five is a
 * list. A missing test file is added only when it was measured as missing and
 * there is room, and it is phrased as "no test file was found", because
 * absence of a paired file is not proof that nothing tests the code.
 *
 * The all-clear is said only when it is true: the findings are loaded, the
 * open ones left take nothing off, and the band agrees. A low score with no
 * open issue on hand (still loading, failed, or just triaged) gets no
 * positive claim.
 */
export function fileVerdict({
  score,
  top,
  hasTestFile,
  historyOnly,
  findingsKnown = true,
  rest = { count: 0, impact: 0, historyImpact: 0 },
}: VerdictInput): Verdict {
  if (historyOnly) return { text: HISTORY_VERDICT, healthy: false };
  if (top.length === 0) {
    if (!findingsKnown) return { text: null, healthy: false };
    if (rest.impact > IMPACT_EPSILON) {
      return rest.historyImpact >= rest.impact - IMPACT_EPSILON
        ? { text: HISTORY_VERDICT, healthy: false }
        : { text: "Its score comes from the issues listed in this panel.", healthy: false };
    }
    const band = score == null ? null : bandForScore(score);
    if (band === "excellent" || band === "good") {
      return {
        text: rest.count > 0 ? "Nothing here takes points off its score." : "Nothing needs fixing here.",
        healthy: true,
      };
    }
    return { text: "No open issue on hand explains this score.", healthy: false };
  }
  const lead = score == null ? "Worth a look" : BAND_LEAD[bandForScore(score)];

  // Group the reasons by kind, keeping the order of the worst finding.
  const kinds: BiomarkerCategory[] = [];
  const functionsByKind = new Map<BiomarkerCategory, Set<string>>();
  const biomarkersByKind = new Map<BiomarkerCategory, Set<string>>();
  for (const f of top) {
    if (isHistoryBiomarker(f.biomarker_type)) continue;
    const raw = biomarkerInfo(f.biomarker_type).category;
    // Size and shape read as one kind of problem to a reader.
    const kind: BiomarkerCategory = raw === "size_and_complexity" ? "structural_complexity" : raw;
    if (!functionsByKind.has(kind)) {
      kinds.push(kind);
      functionsByKind.set(kind, new Set());
      biomarkersByKind.set(kind, new Set());
    }
    functionsByKind.get(kind)!.add(f.function_name ?? "");
    biomarkersByKind.get(kind)!.add(f.biomarker_type);
  }
  const parts = kinds
    .slice(0, 2)
    .map((k) =>
      clause(
        k,
        Math.max(1, functionsByKind.get(k)!.size),
        [...biomarkersByKind.get(k)!].every((b) => b === "large_method"),
      ),
    );
  if (parts.length < 2 && hasTestFile === false && !kinds.includes("test_coverage")) {
    parts.push("no test file was found for it");
  }
  if (parts.length === 0) return { text: `${lead}.`, healthy: false };
  return { text: `${lead}: ${parts.join(" and ")}.`, healthy: false };
}
