/**
 * Biomarker glossary — single source of truth for the human-readable
 * label, category, and short explanation used in tooltips, info popovers,
 * and grouped views across all three health pages.
 *
 * Keep in sync with ``packages/core/src/repowise/core/analysis/health/scoring.py``
 * (the python ``_BIOMARKER_CATEGORY`` map) and ``biomarkers/registry.py``.
 */

export type BiomarkerCategory =
  | "structural_complexity"
  | "size_and_complexity"
  | "duplication"
  | "test_coverage"
  | "organizational";

export interface BiomarkerInfo {
  label: string;
  category: BiomarkerCategory;
  description: string;
}

export const CATEGORY_LABEL: Record<BiomarkerCategory, string> = {
  structural_complexity: "Structural complexity",
  size_and_complexity: "Size & complexity",
  duplication: "Duplication",
  test_coverage: "Test coverage",
  organizational: "Organizational",
};

export const CATEGORY_CAP: Record<BiomarkerCategory, number> = {
  structural_complexity: 3.5,
  size_and_complexity: 2.0,
  duplication: 1.5,
  test_coverage: 2.0,
  organizational: 1.0,
};

export const BIOMARKER_GLOSSARY: Record<string, BiomarkerInfo> = {
  brain_method: {
    label: "Brain method",
    category: "structural_complexity",
    description:
      "A function that knows too much — high cyclomatic complexity, many parameters, and deep nesting all at once. Hard to test, easy to break.",
  },
  nested_complexity: {
    label: "Nested complexity",
    category: "structural_complexity",
    description:
      "Deeply nested control flow (≥4 levels). Cognitive load grows non-linearly with nesting; flatten with early returns or extracted helpers.",
  },
  bumpy_road: {
    label: "Bumpy road",
    category: "structural_complexity",
    description:
      "A function with multiple shallow complexity bumps stitched together. No single block is bad, but the whole reads as a sequence of mini-functions.",
  },
  complex_method: {
    label: "Complex method",
    category: "size_and_complexity",
    description:
      "Cyclomatic complexity above the language threshold. Many independent paths through one function.",
  },
  large_method: {
    label: "Large method",
    category: "size_and_complexity",
    description:
      "A function with too many non-comment lines of code. Even simple logic gets hard to hold in your head past a point.",
  },
  primitive_obsession: {
    label: "Primitive obsession",
    category: "size_and_complexity",
    description:
      "Many primitive parameters where a domain object would carry the same data. Calls become positional and easy to mismatch.",
  },
  dry_violation: {
    label: "DRY violation",
    category: "duplication",
    description:
      "Code blocks duplicated across files. Ranked by co-change frequency — clones that move together are most worth consolidating.",
  },
  untested_hotspot: {
    label: "Untested hotspot",
    category: "test_coverage",
    description:
      "High-churn, centrally depended-on file with no paired test file and low coverage. The riskiest place to leave untested.",
  },
  coverage_gap: {
    label: "Coverage gap",
    category: "test_coverage",
    description:
      "Specific uncovered lines in a file. Surfaced when a coverage report has been ingested.",
  },
  developer_congestion: {
    label: "Developer congestion",
    category: "organizational",
    description:
      "Multiple authors editing the same file frequently — a coordination cost signal. Often points to an unclear module boundary.",
  },
  knowledge_loss: {
    label: "Knowledge loss",
    category: "organizational",
    description:
      "Files whose primary author has reduced or stopped contributing — a bus-factor warning.",
  },
};

export function biomarkerInfo(name: string): BiomarkerInfo {
  return (
    BIOMARKER_GLOSSARY[name] ?? {
      label: name.replace(/_/g, " "),
      category: "size_and_complexity",
      description: "",
    }
  );
}

export function biomarkerLabel(name: string): string {
  return biomarkerInfo(name).label;
}
