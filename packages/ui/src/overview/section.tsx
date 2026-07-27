import * as React from "react";
import { cn } from "../lib/cn";

export interface OverviewSectionProps {
  title: string;
  /** One line under the title. Use it to say what the numbers mean, not what
   *  the section is called again. */
  description?: string;
  /** Right-aligned jump into the page that owns this subject. */
  action?: React.ReactNode;
  /** Drop the top hairline — for the first section under a header that already
   *  closes with one. */
  flush?: boolean;
  /** Anchor target, for deep links that jump to one section of a page. */
  id?: string;
  className?: string;
  children: React.ReactNode;
}

/**
 * The page's only grouping device.
 *
 * The Overview used to be ~13 bordered cards at near-identical weight, which
 * reads as box soup: every block claims the same importance, so none of them
 * lands. A card should mean "discrete object you can act on"; a statistic is
 * not that. Here a hairline plus vertical rhythm carries the grouping at a
 * fraction of the ink, which is what the public repo landing page does and why
 * that page reads calm at higher density than this one.
 *
 * Deliberately not a `<Card>` wrapper with the border switched off: the whole
 * point is that there is no box to configure.
 */
export function OverviewSection({
  title,
  description,
  action,
  flush = false,
  id,
  className,
  children,
}: OverviewSectionProps) {
  return (
    <section
      {...(id ? { id } : {})}
      // Deep-linked sections must not land under a sticky header.
      className={cn(
        id && "scroll-mt-24",
        "flex flex-col gap-3",
        !flush && "border-t border-[var(--color-border-default)] pt-6 sm:pt-8",
        className,
      )}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-base font-semibold tracking-tight text-[var(--color-text-primary)]">
          {title}
        </h2>
        {action}
      </div>
      {description && (
        <p className="max-w-[62ch] text-xs leading-relaxed text-[var(--color-text-tertiary)] [text-wrap:pretty]">
          {description}
        </p>
      )}
      {children}
    </section>
  );
}

/** The standard "go to the page that owns this" link. */
export function SectionLink({
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
      className="whitespace-nowrap text-xs font-medium text-[var(--color-accent-primary)] hover:underline"
    >
      {children} <span aria-hidden>→</span>
    </A>
  );
}
