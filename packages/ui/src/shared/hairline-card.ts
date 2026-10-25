import { cn } from "../lib/cn";

/**
 * The quiet card: a 1px hairline on the page ground, no fill, no shadow, no
 * lift. The hairline strengthens on hover and focus, and a control gets the
 * neutral focus ring. For a discrete object a person opens or acts on, never for
 * a section or a statistic (DESIGN_LANGUAGE "Sections before cards").
 *
 * - `static`: a card nothing in it reacts to.
 * - `group`: a card holding its own controls; it answers hover and keyboard
 *   focus anywhere inside it.
 * - `link`: the card is itself the focusable control (an `<a>` or `<button>`),
 *   so it carries the focus ring.
 *
 * `highlighted` holds the strong hairline with a faint accent ring, for a card
 * the page just scrolled to.
 */
export type HairlineCardVariant = "static" | "group" | "link";

const BASE = "rounded-lg border bg-transparent";
const REST = "border-[var(--color-border-default)]";
const STRONG = "border-[var(--color-border-hover)]";

const VARIANT: Record<HairlineCardVariant, string> = {
  static: REST,
  group: `${REST} transition-colors hover:border-[var(--color-border-hover)] focus-within:border-[var(--color-border-hover)]`,
  link: `${REST} transition-colors hover:border-[var(--color-border-hover)] focus-visible:border-[var(--color-border-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]`,
};

export function hairlineCardClass(
  variant: HairlineCardVariant = "static",
  { highlighted = false }: { highlighted?: boolean } = {},
): string {
  return cn(
    BASE,
    highlighted ? `${STRONG} ring-1 ring-[var(--color-accent-primary)]/40` : VARIANT[variant],
  );
}
