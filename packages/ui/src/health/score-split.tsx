import type { FileSignals } from "@repowise-dev/types/health";
import { InfoTip } from "../shared/info-tip";
import { HISTORY_CATEGORY } from "./biomarker-glossary";

/** The fields of a breakdown category the split reads. */
export interface SplitCategory {
  category: string;
  applied_deduction: number;
}

export interface ScoreSplitParts {
  shape: number;
  history: number;
  coverage: number;
}

export const SHORT_HISTORY_LABEL = "Short history";
export const SHORT_HISTORY_DETAIL =
  "fewer than 5 commits recorded, too few to count, so this score is mostly about code shape";

const COVERAGE_CATEGORIES = new Set(["test_coverage", "test_coverage_gradient"]);

/** A file's deduction in three parts: code shape, change history, test coverage. */
export function scoreSplit(categories: readonly SplitCategory[]): ScoreSplitParts {
  const parts: ScoreSplitParts = { shape: 0, history: 0, coverage: 0 };
  for (const c of categories) {
    const key =
      c.category === HISTORY_CATEGORY
        ? "history"
        : COVERAGE_CATEGORIES.has(c.category)
          ? "coverage"
          : "shape";
    parts[key] += c.applied_deduction;
  }
  return parts;
}

const PARTS: { key: keyof ScoreSplitParts; label: string; tip: string }[] = [
  {
    key: "shape",
    label: "Code shape",
    tip: "Lost to how the code is written: complexity, size, nesting, duplication, error handling. A refactor moves this part.",
  },
  {
    key: "history",
    label: "Change history",
    tip: "Lost to how the file has been changed: past bug fixes, churn, many hands. Editing the code does not move this part; time does.",
  },
  {
    key: "coverage",
    label: "Test coverage",
    tip: "Lost to missing tests, or to lines a coverage report never saw run.",
  },
];

export interface ScoreSplitProps {
  categories: readonly SplitCategory[] | null | undefined;
  signals?: Pick<FileSignals, "history"> | null | undefined;
}

/** What the score lost, split by where it came from, plus the short-history note. */
export function ScoreSplit({ categories, signals }: ScoreSplitProps) {
  const parts = categories && categories.length > 0 ? scoreSplit(categories) : null;
  const short = signals?.history === "short";
  if (!parts && !short) return null;
  return (
    <div role="group" aria-label="What the score lost" className="flex flex-col gap-1.5">
      {parts ? (
        <dl className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
          {PARTS.map((p) => (
            <div key={p.key} className="inline-flex items-center gap-1.5">
              <dt className="inline-flex items-center gap-1 text-[var(--color-text-secondary)]">
                {p.label}
                <InfoTip content={p.tip} label={`About ${p.label.toLowerCase()}`} />
              </dt>
              <dd className="tabular-nums text-[var(--color-text-primary)]">
                {parts[p.key] > 0 ? "−" : ""}
                {parts[p.key].toFixed(2)}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
      {short ? (
        <p className="text-xs text-[var(--color-text-tertiary)]">
          {SHORT_HISTORY_LABEL}: {SHORT_HISTORY_DETAIL}.
        </p>
      ) : null}
    </div>
  );
}
