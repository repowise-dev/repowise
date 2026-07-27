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
  /** The sentence that makes the figure mean something. Load-bearing. */
  children: React.ReactNode;
  /** Optional jump into the page that owns the subject. */
  action?: React.ReactNode;
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
  children,
  action,
}: PageLedeProps) {
  return (
    <div className="flex flex-col">
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
      </div>

      <div className="mt-3.5 max-w-[54ch] text-[13px] leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
        {children}
      </div>

      {action && <div className="mt-4">{action}</div>}
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
