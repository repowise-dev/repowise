import * as React from "react";

export interface PageLedeBand {
  label: string;
  /** Omit for a neutral chip. Only pass a colour when it carries a band —
   *  a health grade, a risk tercile — never to brighten a plain count. */
  color?: string | undefined;
}

export interface PageLedeProps {
  /** Mono micro-label above the figure. */
  label: string;
  /** The figure itself, pre-formatted. */
  value: string;
  /** Colour for the figure. Same rule as `band.color`. */
  valueColor?: string | undefined;
  /** Trailing unit, quiet and small: "out of 10", "of 5,485 files". */
  unit?: string | undefined;
  band?: PageLedeBand | undefined;
  /**
   * An already-styled marker to sit on the figure's baseline, for callers whose
   * band has its own component. Use instead of `band`, not as well: a
   * `PriorityBadge` inside the chip would be two pieces of chrome around one
   * word.
   */
  badge?: React.ReactNode;
  /** The sentence that makes the figure mean something. Load-bearing. */
  children: React.ReactNode;
  /** Optional jump into the page that owns the subject. */
  action?: React.ReactNode;
  /**
   * Where the prose sits.
   *
   * `"stacked"` (default) puts it under the figure — right for a lede that
   * shares its row with something else, which is how Overview uses it.
   * `"beside"` puts it in a column to the right, for a page whose lede owns the
   * full width: at three paragraphs the stacked version pushes everything below
   * it off the fold while leaving most of the row empty.
   */
  layout?: "stacked" | "beside";
  /**
   * Rendered directly under the figure, inside its column. For a second read
   * that belongs to the number itself — a distribution bar, a sparkline — not
   * for another statistic.
   */
  figureFooter?: React.ReactNode;
}

/**
 * The shape a page leads with: one figure large enough to lead, a band chip
 * where a band exists, and the plain-English sentence that makes the figure
 * readable.
 *
 * Extracted from `HealthLede`, which still composes it — the arrangement was
 * being copied by every surface that adopted the section style, and three
 * hand-rolled copies is how the 44 / 48 / 52 sizes drift apart.
 *
 * The prose is not decoration. "329 risks" reads as alarming; "329 static
 * performance risks across 100% of scanned lines, which we rate 9.9 out of
 * 10" reads as informative. Same number.
 */
export function PageLede({
  label,
  value,
  valueColor,
  unit,
  band,
  badge,
  children,
  action,
  layout = "stacked",
  figureFooter,
}: PageLedeProps) {
  const beside = layout === "beside";

  const figure = (
    <div className={beside ? "flex shrink-0 flex-col" : "flex flex-col"}>
      <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        {label}
      </p>

      <div className="mt-2.5 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span
          className="text-[44px] font-semibold leading-none tracking-tight tabular-nums sm:text-5xl"
          style={valueColor ? { color: valueColor } : undefined}
        >
          {value}
        </span>
        {unit && <span className="text-xs text-[var(--color-text-tertiary)]">{unit}</span>}
        {band && (
          <span
            className="rounded-full border px-2.5 py-0.5 text-[11px] font-medium"
            style={
              band.color
                ? {
                    color: band.color,
                    borderColor: `color-mix(in srgb, ${band.color} 40%, transparent)`,
                    background: `color-mix(in srgb, ${band.color} 9%, transparent)`,
                  }
                : {
                    color: "var(--color-text-secondary)",
                    borderColor: "var(--color-border-hover)",
                  }
            }
          >
            {band.label}
          </span>
        )}
        {badge}
      </div>

      {figureFooter && <div className="mt-4">{figureFooter}</div>}
    </div>
  );

  const prose = (
    <div
      className={
        beside
          ? // Two columns from xl. Three paragraphs in one 62ch column runs tall
            // enough to push the page's actual subject below the fold while
            // leaving half the row empty; flowed into two ~48ch columns it is
            // about half the height and fills the space it was already taking
            // up. Paragraphs are kept whole so a sentence never splits across
            // the gap.
            "max-w-[62ch] text-[13px] leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty] xl:max-w-[102ch] xl:columns-2 xl:gap-10 xl:[&>p]:break-inside-avoid"
          : "max-w-[62ch] text-[13px] leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]"
      }
    >
      {children}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );

  if (beside) {
    return (
      // The figure column is fixed so the prose starts at the same x whatever
      // the number is; without it a 7.5 and a 10.0 indent the paragraph
      // differently and the block looks unaligned between repos.
      <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:gap-12">
        <div className="lg:w-[220px]">{figure}</div>
        {prose}
      </div>
    );
  }

  return (
    <div className="flex flex-col">
      {figure}
      <div className="mt-3.5">{prose}</div>
    </div>
  );
}

/** The standard action under a lede. */
export function LedeLink({
  href,
  children,
  LinkComponent,
}: {
  href: string;
  children: React.ReactNode;
  LinkComponent?: React.ElementType | undefined;
}) {
  const A = LinkComponent ?? "a";
  return (
    <A
      href={href}
      className="inline-flex w-fit items-center gap-1 text-sm font-medium text-[var(--color-accent-primary)] hover:underline"
    >
      {children} <span aria-hidden>→</span>
    </A>
  );
}
