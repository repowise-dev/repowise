import type { CSSProperties, ReactNode } from "react";
import { cn } from "@repowise-dev/ui/lib/cn";

/**
 * The shared look of the dialogs that present instead of asking: What's new and
 * the welcome. A deeper ground than the overlay plane, a glow band behind a
 * display heading, and quiet cards keyed by a series-coloured dot.
 *
 * One scale for both: display title, 15px body, 12px caption, 10px micro;
 * 16px gutters on a phone and 32px on a desktop; a 12px shell radius and 8px
 * inside it.
 */

/** The dialog ground, plus a 44px close target in the top corner. Pass to
 *  `DialogContent` with the dialog's own sizing. */
export const MOMENT_GROUND =
  "gap-0 bg-[var(--color-release-ground)] p-0 " +
  "[&>button:last-child]:right-2 [&>button:last-child]:top-2 [&>button:last-child]:grid [&>button:last-child]:size-11 [&>button:last-child]:place-items-center [&>button:last-child]:rounded-lg [&>button:last-child]:bg-[color-mix(in_srgb,var(--color-release-ground)_80%,transparent)] [&>button:last-child]:ring-offset-0";

/** Below 561px a full-height sheet; above, a centred dialog. Add the desktop
 *  width (`min-[561px]:max-w-*`) and height at the call site. */
export const MOMENT_SHEET =
  "left-0 top-0 flex h-dvh max-h-none w-full max-w-none translate-x-0 translate-y-0 flex-col overflow-hidden rounded-none border-0 " +
  "min-[561px]:left-[50%] min-[561px]:top-[50%] min-[561px]:max-h-[85vh] min-[561px]:translate-x-[-50%] min-[561px]:translate-y-[-50%] min-[561px]:rounded-xl min-[561px]:border";

/** Horizontal gutters for every part of a moment: band, body and footer. */
export const MOMENT_GUTTER = "px-4 min-[561px]:px-8";

export const MOMENT_MICRO =
  "font-mono text-[10px] font-normal uppercase leading-4 tracking-[0.12em] text-[var(--color-text-tertiary)]";

/** Body copy: ledes, row titles, highlight titles. */
export const MOMENT_BODY = "text-[15px] leading-6";

/** Secondary copy under a row or figure. */
export const MOMENT_CAPTION = "text-xs leading-5 text-[var(--color-text-secondary)] [text-wrap:pretty]";

/** A counted figure: one size across both moments. */
export const MOMENT_FIGURE =
  "text-[22px] font-semibold leading-7 tabular-nums tracking-tight text-[var(--color-text-primary)]";

/** A short fade and a 4px rise, eased out; reduced motion skips it. */
export const MOMENT_ENTER =
  "motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 motion-safe:duration-200 motion-safe:ease-out";

/** The display heading: one size per breakpoint, balanced so no word sits
 *  alone on the last line. */
export const MOMENT_DISPLAY =
  "font-serif text-[32px] font-normal leading-[1.1] tracking-tight text-[var(--color-text-primary)] [text-wrap:balance] min-[561px]:text-[44px]";

/** A card on the moment ground. */
export const MOMENT_CARD =
  "rounded-lg border border-[var(--color-border-default)] bg-[var(--color-release-card)]";

/** A quiet text action: links and copy buttons inside a moment. */
export const MOMENT_LINK =
  "rounded text-xs font-medium text-[var(--color-text-primary)] underline decoration-[var(--color-border-hover)] underline-offset-[3px] hover:decoration-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]";

/** A micro-label with the series dot that keys a card. */
export function MomentLabel({ color, children }: { color: string; children: ReactNode }) {
  return (
    <span className={cn(MOMENT_MICRO, "flex items-center gap-2")}>
      <span aria-hidden className="size-1.5 shrink-0 rounded-full" style={{ background: color }} />
      {children}
    </span>
  );
}

/** The glow bands, fading into the dialog ground. Solid stops, checked by
 *  contrast-check.py. What's new runs orange into plum; the welcome runs plum
 *  into blue. Each reaches the ground before the far edge, so it reads as a
 *  wash behind the heading. */
const BANDS = {
  release:
    "linear-gradient(120deg, var(--color-release-glow-1) 0%, var(--color-release-glow-2) 45%, var(--color-release-ground) 90%)",
  welcome:
    "linear-gradient(120deg, var(--color-release-glow-2) 0%, var(--color-release-glow-3) 45%, var(--color-release-ground) 90%)",
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
      className={cn(
        "shrink-0 border-b border-[var(--color-border-default)] pb-6 pt-6 min-[561px]:pt-8",
        MOMENT_GUTTER,
        className,
      )}
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
