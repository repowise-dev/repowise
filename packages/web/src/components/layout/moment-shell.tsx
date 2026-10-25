import type { CSSProperties, ReactNode } from "react";
import { cn } from "@repowise-dev/ui/lib/cn";

/**
 * The shared look of the dialogs that present rather than ask: What's new and
 * the welcome. A deeper ground than the overlay plane, a glow band behind a
 * display heading, and cards with a series-coloured left edge.
 */

/** The dialog ground. Pass to `DialogContent` with the dialog's own sizing. */
export const MOMENT_GROUND = "gap-0 bg-[var(--color-release-ground)] p-0";

export const MOMENT_MICRO =
  "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";

/** A gentle rise on open; the global reduced-motion rule flattens it. */
export const MOMENT_ENTER =
  "motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 duration-500";

/** The display heading's type, without its weight. */
export const MOMENT_DISPLAY =
  "font-serif text-4xl tracking-tight text-[var(--color-text-primary)] sm:text-5xl";

/** A card on the moment ground; set `borderLeftColor` to its series colour. */
export const MOMENT_CARD =
  "rounded-lg border border-[var(--color-border-default)] border-l-[3px] bg-[var(--color-release-card)]";

/** The glow bands, fading into the dialog ground. Solid stops, checked by
 *  contrast-check.py. What's new runs orange into plum; the welcome runs plum
 *  into blue. */
const BANDS = {
  release:
    "linear-gradient(120deg, var(--color-release-glow-1) 0%, var(--color-release-glow-2) 55%, var(--color-release-ground) 100%)",
  welcome:
    "linear-gradient(120deg, var(--color-release-glow-2) 0%, var(--color-release-glow-3) 55%, var(--color-release-ground) 100%)",
} as const;

export type MomentTone = keyof typeof BANDS;

/** The band at the top of a moment dialog. */
export function MomentBand({
  tone,
  className,
  children,
}: {
  tone: MomentTone;
  className?: string;
  children: ReactNode;
}) {
  return (
    <header
      className={cn("border-b border-[var(--color-border-default)] px-6 pb-6 pt-6 sm:px-8", className)}
      style={{ backgroundImage: BANDS[tone] }}
    >
      {children}
    </header>
  );
}

/** Ink fading into plum and blue, for the emphasised words of a display heading. */
export const MOMENT_EMPHASIS: CSSProperties = {
  backgroundImage:
    "linear-gradient(100deg, var(--color-text-primary) 10%, var(--color-series-2) 70%, var(--color-series-3) 115%)",
  WebkitBackgroundClip: "text",
  backgroundClip: "text",
  color: "transparent",
};
