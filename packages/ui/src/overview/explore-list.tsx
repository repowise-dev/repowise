import * as React from "react";

export interface ExploreEntry {
  key: string;
  label: string;
  /** What is actually there, ideally with a live figure. "5,520 pages across
   *  115 modules" beats "Documentation for your code". */
  description: string;
  href: string;
  /** Small qualifier after the label, e.g. "Pro" or "beta". */
  tag?: string;
}

/**
 * Front doors to the pages that own each subject.
 *
 * Replaces four equal-sized cards that were four different species: a donut, a
 * marketing blurb with no data in it, a data list, and a text input. A link
 * list scans faster, survives any number of entries, and does not imply that a
 * blurb and a chart are the same kind of thing.
 *
 * Each row carries a real figure rather than a description of the feature,
 * which makes the list double as a second, quieter set of statistics.
 */
export function ExploreList({
  entries,
  LinkComponent,
}: {
  entries: ExploreEntry[];
  LinkComponent?: React.ElementType;
}) {
  const A = LinkComponent ?? "a";
  if (entries.length === 0) return null;

  return (
    <ul className="m-0 list-none divide-y divide-[var(--color-border-default)] border-y border-[var(--color-border-default)] p-0">
      {entries.map((e) => (
        <li key={e.key}>
          <A
            href={e.href}
            className="group flex flex-col gap-0.5 py-2.5 text-xs no-underline transition-colors hover:bg-[var(--color-bg-elevated)] sm:flex-row sm:items-baseline sm:gap-4"
          >
            <span className="flex shrink-0 items-center gap-2 sm:w-44">
              {/* The label carries the accent at rest, not only on hover. A row
                  whose only affordance is a hover state reads as a table, and
                  on touch there is no hover at all — the whole list would look
                  like static data. Accent is this system's link colour, so this
                  is the convention rather than an exception to it. */}
              <span className="font-medium text-[var(--color-accent-primary)] group-hover:underline">
                {e.label}
              </span>
              {e.tag && (
                <span className="rounded-full bg-[var(--color-bg-inset)] px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
                  {e.tag}
                </span>
              )}
            </span>
            <span className="min-w-0 flex-1 text-[var(--color-text-secondary)] [text-wrap:pretty]">
              {e.description}
            </span>
            <span
              aria-hidden
              className="hidden shrink-0 self-center text-[var(--color-text-tertiary)] transition-transform group-hover:translate-x-0.5 group-hover:text-[var(--color-accent-primary)] sm:block"
            >
              →
            </span>
          </A>
        </li>
      ))}
    </ul>
  );
}
