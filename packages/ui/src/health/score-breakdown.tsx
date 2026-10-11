import {
  CATEGORY_CAP,
  CATEGORY_LABEL,
  biomarkerLabel,
  type BiomarkerCategory,
} from "./biomarker-glossary";
import { type Severity } from "./tokens";
import { ImpactFigure } from "./impact-figure";
import { SeverityMark } from "./severity-mark";

/**
 * The proportion bars carry no colour.
 *
 * They were all the deduction red, which on a file with seven scoring
 * categories is seven red bars: the colour said "bad" seven times and ranked
 * nothing. Nothing was gained by tying the tone to severity either, because
 * the bar's own length already is the magnitude and the rows are sorted by it.
 * So the bar states one thing, in one channel, and the red is left to mean
 * something where it is still used.
 */
const BAR_FILL = "color-mix(in srgb, var(--color-text-tertiary) 55%, transparent)";
/** Fainter again when there is no cap, so the bar's scale is approximate. */
const BAR_FILL_UNCAPPED = "color-mix(in srgb, var(--color-text-tertiary) 30%, transparent)";

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
  /** Cap the scorer actually enforced. Optional only because payloads from
   *  older servers predate the field; when present it always wins over the
   *  glossary's `CATEGORY_CAP` fallback. */
  cap?: number | null;
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
      {/* The arithmetic, once, quietly. This led with the score again as a
          large coloured badge, a second copy of the figure the surface above
          it already shows at four times the size. What this section is for is
          the subtraction, not the total. */}
      <p className="text-xs tabular-nums text-[var(--color-text-tertiary)]">
        10.0 − {totalDeduction.toFixed(2)} = {score.toFixed(1)}
      </p>

      <div className="space-y-2.5">
        {[...categories]
          .sort((a, b) => {
            if (b.applied_deduction !== a.applied_deduction) {
              return b.applied_deduction - a.applied_deduction;
            }
            const capA =
              a.cap ?? CATEGORY_CAP[a.category as BiomarkerCategory] ?? 0;
            const capB =
              b.cap ?? CATEGORY_CAP[b.category as BiomarkerCategory] ?? 0;
            return capB - capA;
          })
          .map((c) => {
          const label =
            CATEGORY_LABEL[c.category as BiomarkerCategory] ?? c.category;
          // Prefer the server-supplied cap: it is the value the scorer
          // actually enforced, so a cap retune in scoring.py renders
          // correctly without a UI release. The glossary constant is only a
          // fallback for older-server payloads that predate the `cap` field.
          const cap = c.cap ?? CATEGORY_CAP[c.category as BiomarkerCategory];
          const pct = Math.min(
            100,
            (Math.abs(c.applied_deduction) /
              Math.max(Math.abs(cap ?? c.applied_deduction), 0.01)) *
              100,
          );
          return (
            // Hairline-separated, not a card each. Every category already has a
            // label, a figure and a proportion bar; a border around that is a
            // fourth way of saying "this is a group".
            <div
              key={c.category}
              className="border-t border-[var(--color-border-default)] px-1 pt-3 first:border-t-0 first:pt-0"
            >
              <div className="flex items-center justify-between gap-2 text-xs">
                <span className="font-medium text-[var(--color-text-primary)]">
                  {label}
                </span>
                <span
                  className="cursor-help tabular-nums text-[var(--color-text-tertiary)]"
                  title="The cap is the most this category is allowed to subtract from the 10-point score, no matter how many findings it has."
                >
                  −{c.applied_deduction.toFixed(2)}
                  {cap != null ? <> / cap −{cap.toFixed(1)}</> : null}
                  {c.capped ? <span className="ml-1 text-[var(--color-warning)]" title="Raw deductions exceeded the cap; only the cap was subtracted.">(capped)</span> : null}
                </span>
              </div>
              <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
                <div
                  className="h-full"
                  title={
                    cap == null
                      ? "No defined cap for this category — bar scale is approximate."
                      : undefined
                  }
                  style={{
                    width: `${pct}%`,
                    background: cap == null ? BAR_FILL_UNCAPPED : BAR_FILL,
                  }}
                />
              </div>
              {c.findings.length > 0 && (
                <ul className="mt-2 space-y-1">
                  {c.findings.slice(0, 6).map((f) => (
                    <li
                      key={f.id}
                      className="flex flex-wrap items-baseline gap-x-2 text-xs"
                    >
                      <SeverityMark severity={f.severity} />
                      <span className="font-medium text-[var(--color-text-primary)]">
                        {biomarkerLabel(f.biomarker_type)}
                      </span>
                      {f.function_name ? (
                        <span className="font-mono text-[var(--color-text-tertiary)]">
                          {f.function_name}
                        </span>
                      ) : null}
                      {/* Muted: the severity mark at the head of the row is
                          already the colour channel, and six red numbers under
                          a red bar rank nothing. */}
                      <ImpactFigure impact={f.applied_impact} tone="muted" className="ml-auto" />
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
