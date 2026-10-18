import * as React from "react";
import type { ExploreEntry } from "./explore-list";

export interface ExploreCard {
  key: string;
  label: string;
  /** One live figure. Omitted when the payload has none for this door. */
  value?: string;
  /** Set the figure in mono, for machine facts such as a commit hash. */
  mono?: boolean;
  /** What the figure counts, or what the page holds when there is no figure. */
  caption: string;
  href: string;
}

export interface ExploreCardsProps {
  cards: ExploreCard[];
  /** Further doors, shown as one quiet line of links under the cards. */
  more?: ExploreEntry[];
  /** Label before the further doors. */
  moreLabel?: string;
  LinkComponent?: React.ElementType | undefined;
}

/**
 * The main pages as a row of doors, each carrying one figure from the data
 * the page already loaded. Every card is a link, so the card is the object
 * that responds.
 */
export function ExploreCards({ cards, more = [], moreLabel = "Also", LinkComponent }: ExploreCardsProps) {
  const A = LinkComponent ?? "a";
  if (cards.length === 0 && more.length === 0) return null;
  return (
    <div className="flex flex-col gap-3">
      <ul className="m-0 grid list-none grid-cols-1 gap-3 p-0 sm:grid-cols-2 lg:grid-cols-5">
        {cards.map((c) => (
          <li key={c.key} className="min-w-0 sm:last:odd:col-span-2 lg:last:odd:col-span-1">
            <A
              href={c.href}
              className="group flex h-full flex-col gap-1 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-4 py-3.5 no-underline transition-colors hover:border-[var(--color-border-hover)] hover:bg-[var(--color-bg-elevated)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
            >
              <span className="flex items-center justify-between gap-2 text-xs font-medium text-[var(--color-accent-primary)]">
                {c.label}
                <span
                  aria-hidden
                  className="text-[var(--color-text-tertiary)] transition-transform group-hover:translate-x-0.5 group-hover:text-[var(--color-accent-primary)]"
                >
                  →
                </span>
              </span>
              {c.value && (
                <span
                  className={`text-[22px] font-semibold leading-tight tabular-nums text-[var(--color-text-primary)] ${
                    c.mono ? "font-mono" : ""
                  }`}
                >
                  {c.value}
                </span>
              )}
              <span className="text-xs text-[var(--color-text-tertiary)] [text-wrap:pretty]">
                {c.caption}
              </span>
            </A>
          </li>
        ))}
      </ul>
      {more.length > 0 && (
        <p className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-xs text-[var(--color-text-tertiary)]">
          <span>{moreLabel}</span>
          {more.map((e) => (
            <A
              key={e.key}
              href={e.href}
              title={e.description}
              className="font-medium text-[var(--color-accent-primary)] hover:underline"
            >
              {e.label}
            </A>
          ))}
        </p>
      )}
    </div>
  );
}
