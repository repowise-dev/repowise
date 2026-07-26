import * as React from "react";

export interface RibbonStat {
  label: string;
  value: string;
  hint?: string;
}

/**
 * A row of figures separated by hairlines rather than boxed into cards.
 *
 * The stats page used to be ~25 near-identical bordered tiles, which reads as
 * box soup: every figure claims the same visual weight, so none of them lands.
 * Rules carry the same grouping at a fraction of the ink, which is why the
 * public repo pages use them for exactly this job.
 *
 * Rendered as a `<dl>` because that is what it is — labelled values, not a
 * layout grid.
 */
export function StatRibbon({ stats }: { stats: RibbonStat[] }) {
  const shown = stats.filter((s) => s.value);
  if (shown.length === 0) return null;

  return (
    <dl className="grid grid-cols-2 border-y border-[var(--color-border-default)] sm:grid-cols-3 lg:grid-cols-5">
      {shown.map((s, i) => (
        <div
          key={s.label}
          title={s.hint}
          className={[
            "px-4 py-3.5",
            s.hint ? "cursor-help" : "",
            // Hairlines between cells only — the outer edges come from the
            // wrapper's border-y, so cells never double up on the boundary.
            "border-[var(--color-border-default)]",
            i % 2 === 1 ? "border-l" : "",
            i >= 2 ? "border-t" : "",
            "sm:border-l sm:border-t-0",
            i % 3 === 0 ? "sm:border-l-0" : "",
            i >= 3 ? "sm:border-t" : "",
            "lg:border-l lg:border-t-0",
            i % 5 === 0 ? "lg:border-l-0" : "",
          ]
            .filter(Boolean)
            .join(" ")}
        >
          <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
            {s.label}
          </dt>
          <dd className="mt-1 text-xl font-semibold tabular-nums text-[var(--color-text-primary)]">
            {s.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}
