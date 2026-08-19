import * as React from "react";

export interface RibbonStat {
  label: string;
  value: string;
  hint?: string;
  /**
   * Tailwind text-colour class for the value. Only for figures that carry a
   * band — a health grade, a risk tercile — never to brighten a plain count.
   * Added for Code Health, where a 3.1 and a 9.4 in the same row read as the
   * same news without it.
   */
  valueColor?: string | undefined;
  /** A quiet second line under the value — a delta, a comparison, a filename. */
  sub?: string | undefined;
  /** Tailwind text-colour class for `sub`. Same rule as `valueColor`. */
  subColor?: string | undefined;
  /** Optional jump to the page that owns this figure. Added because the
   *  Overview replaced a strip of *linked* KPI tiles with this component, and
   *  without it Files and Symbols lost their only entry point from that page. */
  href?: string;
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
export function StatRibbon({
  stats,
  LinkComponent,
}: {
  stats: RibbonStat[];
  /** Router link for `href` entries; defaults to a plain anchor. */
  LinkComponent?: React.ElementType | undefined;
}) {
  const shown = stats.filter((s) => s.value);
  if (shown.length === 0) return null;
  const A = LinkComponent ?? "a";

  return (
    <dl className="grid grid-cols-2 border-y border-[var(--color-border-default)] sm:grid-cols-3 lg:grid-cols-5">
      {shown.map((s, i) => (
        <div
          key={s.label}
          title={s.hint}
          className={[
            s.href ? "" : "px-4 py-3.5",
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
          {s.href ? (
            // The link wraps the whole cell rather than the value, so the
            // padding is part of the hit target instead of a dead margin
            // around it.
            <A
              href={s.href}
              className="group block px-4 py-3.5 no-underline transition-colors hover:bg-[var(--color-bg-elevated)]"
            >
              <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                {s.label}
              </dt>
              <dd
                className={`mt-1 text-xl font-semibold tabular-nums group-hover:text-[var(--color-accent-primary)] ${
                  s.valueColor ?? "text-[var(--color-text-primary)]"
                }`}
              >
                {s.value}
              </dd>
            </A>
          ) : (
            <>
              <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                {s.label}
              </dt>
              <dd
                className={`mt-1 text-xl font-semibold tabular-nums ${
                  s.valueColor ?? "text-[var(--color-text-primary)]"
                }`}
              >
                {s.value}
              </dd>
              {s.sub && (
                <dd
                  className={`mt-0.5 text-xs tabular-nums ${
                    s.subColor ?? "text-[var(--color-text-tertiary)]"
                  }`}
                >
                  {s.sub}
                </dd>
              )}
            </>
          )}
        </div>
      ))}
    </dl>
  );
}
