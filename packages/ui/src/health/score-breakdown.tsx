import {
  CATEGORY_CAP,
  CATEGORY_LABEL,
  biomarkerLabel,
  type BiomarkerCategory,
} from "./biomarker-glossary";
import { SEVERITY_CHIP, SEVERITY_LABEL, scoreBadgeClass, type Severity } from "./tokens";

export interface ScoreBreakdownCategoryFinding {
  id: string;
  biomarker_type: string;
  severity: Severity;
  raw_impact: number;
  applied_impact: number;
  function_name: string | null;
  reason: string;
}

export interface ScoreBreakdownCategory {
  category: BiomarkerCategory | string;
  cap: number;
  raw_deduction: number;
  applied_deduction: number;
  capped: boolean;
  finding_count: number;
  findings: ScoreBreakdownCategoryFinding[];
}

export interface ScoreBreakdownProps {
  score: number;
  totalDeduction: number;
  categories: ScoreBreakdownCategory[];
}

export function ScoreBreakdown({
  score,
  totalDeduction,
  categories,
}: ScoreBreakdownProps) {
  return (
    <div className="space-y-3">
      <div className="flex items-baseline gap-3">
        <span
          className={`inline-flex items-center rounded-md px-2 py-1 text-lg font-bold tabular-nums ${scoreBadgeClass(score)}`}
        >
          {score.toFixed(1)}
          <span className="ml-0.5 text-xs font-normal opacity-70">/10</span>
        </span>
        <span className="text-xs text-[var(--color-text-tertiary)]">
          10.0 − {totalDeduction.toFixed(2)} = {score.toFixed(2)}
        </span>
      </div>

      <div className="space-y-2.5">
        {[...categories]
          .sort((a, b) => {
            if (b.applied_deduction !== a.applied_deduction) {
              return b.applied_deduction - a.applied_deduction;
            }
            const capA =
              CATEGORY_CAP[a.category as BiomarkerCategory] ?? a.cap;
            const capB =
              CATEGORY_CAP[b.category as BiomarkerCategory] ?? b.cap;
            return capB - capA;
          })
          .map((c) => {
          const label =
            CATEGORY_LABEL[c.category as BiomarkerCategory] ?? c.category;
          const cap = CATEGORY_CAP[c.category as BiomarkerCategory] ?? c.cap;
          const pct = Math.min(100, (c.applied_deduction / Math.max(cap, 0.01)) * 100);
          return (
            <div
              key={c.category}
              className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3"
            >
              <div className="flex items-center justify-between gap-2 text-xs">
                <span className="font-medium text-[var(--color-text-primary)]">
                  {label}
                </span>
                <span className="tabular-nums text-[var(--color-text-tertiary)]">
                  −{c.applied_deduction.toFixed(2)} / cap −{cap.toFixed(1)}
                  {c.capped ? <span className="ml-1 text-[var(--color-warning)]" title="Capped">(capped)</span> : null}
                </span>
              </div>
              <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-bg-muted)]">
                <div
                  className="h-full bg-[var(--color-error)]/70"
                  style={{ width: `${pct}%` }}
                />
              </div>
              {c.findings.length > 0 && (
                <ul className="mt-2 space-y-1">
                  {c.findings.slice(0, 6).map((f) => (
                    <li
                      key={f.id}
                      className="flex flex-wrap items-baseline gap-x-2 text-xs"
                    >
                      <span
                        className={`inline-block rounded px-1.5 py-px text-[10px] uppercase font-semibold ${SEVERITY_CHIP[f.severity]}`}
                      >
                        {SEVERITY_LABEL[f.severity]}
                      </span>
                      <span className="font-medium text-[var(--color-text-primary)]">
                        {biomarkerLabel(f.biomarker_type)}
                      </span>
                      {f.function_name ? (
                        <span className="font-mono text-[var(--color-text-tertiary)]">
                          {f.function_name}
                        </span>
                      ) : null}
                      <span className="ml-auto tabular-nums text-[var(--color-error)]">
                        −{f.applied_impact.toFixed(2)}
                      </span>
                    </li>
                  ))}
                  {c.findings.length > 6 ? (
                    <li className="text-xs text-[var(--color-text-tertiary)]">
                      + {c.findings.length - 6} more
                    </li>
                  ) : null}
                </ul>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
