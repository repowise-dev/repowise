import { cn } from "../lib/cn";
import { formatLOC } from "../lib/format";
import type { RiskDriver } from "@repowise-dev/types/git";

export interface RiskDriverBreakdownProps {
  drivers: RiskDriver[];
  className?: string;
}

/**
 * Restates a driver as what it did, not as what it is.
 *
 * The server's labels describe the *input* relative to the model's baseline
 * commit: "more files than baseline". The bar and the signed figure beside it
 * describe the *effect* on the score. On a real commit those two disagree on
 * three rows out of seven — "more files than baseline" carries −1.12 and
 * renders green — so a reader following the colour reads the row backwards.
 * Naming the measured value instead ("311 files touched") leaves the direction
 * to the one place that encodes it.
 *
 * Falls back to the server's label for any feature this does not know, so a
 * new driver appears with its own wording rather than disappearing.
 */
export function describeDriver(driver: RiskDriver): string {
  const v = driver.value;
  if (v === null) return driver.label;
  const n = Math.round(v);
  switch (driver.feature) {
    case "la":
      return `${formatLOC(n)} lines added`;
    case "ld":
      return n === 0 ? "nothing deleted" : `${formatLOC(n)} lines deleted`;
    case "nf":
      return `${n.toLocaleString()} ${n === 1 ? "file" : "files"} touched`;
    case "nd":
      return `${n.toLocaleString()} ${n === 1 ? "directory" : "directories"} touched`;
    case "ns":
      return `${n.toLocaleString()} ${n === 1 ? "subsystem" : "subsystems"} touched`;
    case "entropy":
      // Shannon entropy of the per-file churn, in bits and not normalised:
      // k equally-changed files score log2(k), so every bit is a doubling of
      // how widely the change spread.
      return `spread of ${v.toFixed(2)} bits across its files`;
    case "exp":
      return n === 0
        ? "author's first commit here"
        : `author had ${n.toLocaleString()} prior ${n === 1 ? "commit" : "commits"}`;
    default:
      return driver.label;
  }
}

/**
 * Per-feature change-risk attribution.
 *
 * The model is a linear logistic, so each driver's signed contribution is
 * exact: positive (red, right of the baseline) pushed the score up, negative
 * (green, left) pulled it down. Bars are scaled to the strongest contribution
 * in the set. Features whose value is unknown are skipped.
 *
 * Rendered as a table rather than a flex row of `w-28 truncate` labels. The
 * label is the primary column here, and it was losing the end of every driver
 * it named to an ellipsis at exactly the width where the wording stops being
 * guessable.
 */
export function RiskDriverBreakdown({ drivers, className }: RiskDriverBreakdownProps) {
  const shown = drivers.filter((d) => d.value !== null);
  if (shown.length === 0) return null;

  const maxAbs = Math.max(...shown.map((d) => Math.abs(d.contribution)), 1e-6);

  return (
    <table className={cn("w-full border-collapse text-xs", className)}>
      <caption className="sr-only">
        Each measurement the change-risk model reads, and its signed contribution
        to this commit&apos;s raw score
      </caption>
      <thead>
        <tr>
          <th
            scope="col"
            className="border-b border-[var(--color-border-default)] pb-2 pr-3 text-left font-mono text-[10px] font-normal uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]"
          >
            Measurement
          </th>
          <th className="max-sm:hidden" />
          <th
            scope="col"
            title="Signed points on the raw 0 to 10 score, against the model's baseline commit"
            className="cursor-help border-b border-[var(--color-border-default)] pb-2 pl-3 text-right font-mono text-[10px] font-normal uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]"
          >
            Points
          </th>
        </tr>
      </thead>
      <tbody>
        {shown.map((d) => {
          const width = (Math.abs(d.contribution) / maxAbs) * 50; // half-width per side
          const raises = d.contribution >= 0;
          return (
            <tr key={d.feature} className="border-b border-[var(--color-border-default)] last:border-0">
              <td className="py-2 pr-3 align-middle text-[var(--color-text-secondary)] [text-wrap:pretty]">
                {describeDriver(d)}
              </td>
              {/* The bar is the redundant encoding — the signed figure and its
                  colour already carry direction — so it is the first thing to
                  yield when the label needs the width. */}
              <td className="w-[120px] px-3 align-middle max-sm:hidden">
                <span className="relative block h-1.5 rounded-full bg-[var(--color-bg-inset)]">
                  <span
                    aria-hidden
                    className="absolute inset-y-[-2px] left-1/2 w-px bg-[var(--color-border-hover)]"
                  />
                  <span
                    className={cn(
                      "absolute inset-y-0 rounded-full",
                      raises ? "bg-[var(--color-error)]" : "bg-[var(--color-success)]",
                    )}
                    style={
                      raises
                        ? { left: "50%", width: `${width}%` }
                        : { right: "50%", width: `${width}%` }
                    }
                  />
                </span>
              </td>
              <td
                className={cn(
                  "whitespace-nowrap py-2 pl-3 text-right align-middle tabular-nums",
                  raises ? "text-[var(--color-error)]" : "text-[var(--color-success)]",
                )}
              >
                {raises ? "+" : "−"}
                {Math.abs(d.contribution).toFixed(2)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
