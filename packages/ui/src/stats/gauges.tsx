import * as React from "react";

export interface Gauge {
  label: string;
  value: string;
  /** Quiet unit after the value: "days", "per 100". */
  unit?: string | undefined;
  /** Exact figure behind a shortened value, for `title` and the accessible name. */
  exact?: string | undefined;
  /** The line under the value that says what it was measured against. */
  sub?: string | undefined;
  /** A small drawn mark between value and sub (streak ticks, a bar pair). */
  mark?: React.ReactNode;
  href?: string | undefined;
}

// Static class names so Tailwind sees every column count it may need.
const LG_COLS = ["", "lg:grid-cols-1", "lg:grid-cols-2", "lg:grid-cols-3", "lg:grid-cols-4", "lg:grid-cols-5", "lg:grid-cols-6"];

/** Inner hairlines only, for 2 columns on phones, 3 on tablets and one row on desktop. */
function hairlines(i: number, n: number): string {
  const lg = Math.min(n, 6);
  return [
    i % 2 ? "border-l pl-4" : "border-l-0 pl-0",
    i >= 2 ? "border-t" : "border-t-0",
    i % 3 ? "sm:border-l sm:pl-4" : "sm:border-l-0 sm:pl-0",
    i >= 3 ? "sm:border-t" : "sm:border-t-0",
    i % lg ? "lg:border-l lg:pl-5" : "lg:border-l-0 lg:pl-0",
    i >= lg ? "lg:border-t" : "lg:border-t-0",
  ].join(" ");
}

/**
 * A row of instrument readouts: label, value with its unit, an optional drawn
 * mark, and the line saying what it was measured against.
 *
 * Hairlines, not cards. `StatRibbon` stays the shared ribbon for one-line
 * figures; this adds the unit, the mark and the exact-figure title the Stats
 * page needs and nothing else does.
 */
export function Gauges({
  gauges,
  LinkComponent,
  className = "",
}: {
  gauges: Gauge[];
  LinkComponent?: React.ElementType | undefined;
  className?: string;
}) {
  const shown = gauges.filter((g) => g.value);
  if (shown.length === 0) return null;
  const A = LinkComponent ?? "a";
  const n = shown.length;

  return (
    <dl className={`grid grid-cols-2 sm:grid-cols-3 ${LG_COLS[Math.min(n, 6)]} ${className}`}>
      {shown.map((g, i) => {
        const value = (
          <span
            className="text-[22px] font-semibold leading-tight tracking-[-0.01em] tabular-nums text-[var(--color-text-primary)]"
            title={g.exact}
          >
            {/* A shortened figure reads aloud as its exact value. */}
            <span aria-hidden={g.exact ? true : undefined}>{g.value}</span>
            {g.exact && <span className="sr-only">{g.exact}</span>}
            {g.unit && (
              <span className="ml-1 text-xs font-normal tracking-normal text-[var(--color-text-secondary)]">
                {g.unit}
              </span>
            )}
          </span>
        );
        return (
          <div
            key={g.label}
            className={`min-w-0 border-[var(--color-border-default)] py-3.5 pr-3 ${hairlines(i, n)}`}
          >
            <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
              {g.label}
            </dt>
            <dd className="mt-1.5">
              {g.href ? (
                <A
                  href={g.href}
                  className="rounded-sm no-underline hover:[&>span]:text-[var(--color-accent-primary)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-focus-ring)]"
                >
                  {value}
                </A>
              ) : (
                value
              )}
            </dd>
            {g.mark && (
              <dd className="mt-2" aria-hidden>
                {g.mark}
              </dd>
            )}
            {g.sub && (
              <dd className="mt-1 text-xs leading-snug text-[var(--color-text-tertiary)]">{g.sub}</dd>
            )}
          </div>
        );
      })}
    </dl>
  );
}
