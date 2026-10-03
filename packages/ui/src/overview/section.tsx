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
  /** Turn the heading into a toggle. Native ``<details>``, so the section
   *  stays a server component and keyboard handling comes free. */
  collapsible?: boolean;
  /** Only read when *collapsible*. Defaults to open: a section that hides its
   *  content by default has to earn it. */
  defaultOpen?: boolean;
  /** Right-aligned on the toggle, e.g. a row count, so a collapsed section
   *  still says how much is behind it. */
  hint?: React.ReactNode;
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
  collapsible = false,
  defaultOpen = true,
  hint,
  className,
  children,
}: OverviewSectionProps) {
  const heading = (
    <h2 className="text-base font-semibold tracking-tight text-[var(--color-text-primary)]">
      {title}
    </h2>
  );
  const body = (
    <>
      {description && (
        <p className="max-w-[62ch] text-xs leading-relaxed text-[var(--color-text-tertiary)] [text-wrap:pretty]">
          {description}
        </p>
      )}
      {children}
    </>
  );

  if (collapsible) {
    return (
      <section
        {...(id ? { id } : {})}
        className={cn(
          id && "scroll-mt-24",
          !flush && "border-t border-[var(--color-border-default)] pt-6 sm:pt-8",
          className,
        )}
      >
        <details open={defaultOpen} className="group flex flex-col gap-3">
          <summary className="flex cursor-pointer list-none flex-wrap items-baseline gap-x-2 gap-y-1 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]">
            <span
              className="text-[var(--color-text-tertiary)] transition-transform group-open:rotate-90"
              aria-hidden
            >
              ▸
            </span>
            {heading}
            {hint != null && (
              <span className="ml-auto text-xs text-[var(--color-text-tertiary)]">{hint}</span>
            )}
          </summary>
          <div className="mt-3 flex flex-col gap-3">{body}</div>
        </details>
      </section>
    );
  }

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
        {heading}
        {action}
      </div>
      {body}
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
