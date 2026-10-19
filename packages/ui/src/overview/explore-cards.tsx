import * as React from "react";
import { CopyLine } from "../settings/settings-primitives";
import type { ExploreEntry } from "./explore-list";

export interface ExploreCard {
  key: string;
  label: string;
  /** One live figure. Omitted when the payload has none for this door. */
  value?: string;
  /** What the figure counts, or what the page holds when there is no figure. */
  caption: string;
  /** A command that fills the page, shown with a copy button. */
  command?: string;
  href: string;
}

export interface ExploreCardsProps {
  cards: ExploreCard[];
  /** Further doors, shown as one quiet line of links under the cards. */
  more?: ExploreEntry[];
  /** Label before the further doors. */
  moreLabel?: string;
  /** What a card's command is, for its copy button's accessible name. */
  commandLabel?: string;
  LinkComponent?: React.ElementType | undefined;
}

/**
 * The main pages as a grid of doors, each carrying one figure from the data
 * the page already loaded. Hairline outlines on the page ground, no fill: the
 * card's link fills it, and hover strengthens the hairline rather than lifting
 * it. A card with a command keeps the copy button outside the link.
 */
export function ExploreCards({
  cards,
  more = [],
  moreLabel = "Also",
  commandLabel = "the command",
  LinkComponent,
}: ExploreCardsProps) {
  const A = LinkComponent ?? "a";
  if (cards.length === 0 && more.length === 0) return null;
  return (
    <div className="flex flex-col gap-3">
      <ul className="m-0 grid list-none grid-cols-1 gap-3 p-0 sm:grid-cols-2 lg:grid-cols-3">
        {cards.map((c) => (
          <li
            key={c.key}
            className="group flex min-w-0 flex-col rounded-lg border border-[var(--color-border-default)] bg-transparent transition-colors hover:border-[var(--color-border-hover)] focus-within:border-[var(--color-border-hover)]"
          >
            <A
              href={c.href}
              className="flex flex-1 flex-col gap-1.5 rounded-lg px-5 py-4 no-underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
            >
              <span className="flex items-center justify-between gap-2 text-xs font-medium text-[var(--color-text-secondary)]">
                {c.label}
                <span
                  aria-hidden
                  className="text-[var(--color-text-tertiary)] transition-transform group-hover:translate-x-0.5 group-has-[a:focus-visible]:translate-x-0.5"
                >
                  →
                </span>
              </span>
              {c.value && (
                <span className="font-mono text-[22px] font-semibold leading-tight tabular-nums text-[var(--color-text-primary)]">
                  {c.value}
                </span>
              )}
              <span className="text-xs text-[var(--color-text-tertiary)] [text-wrap:pretty]">
                {c.caption}
              </span>
            </A>
            {c.command && (
              <div className="px-5 pb-4">
                <CopyLine value={c.command} copyLabel={commandLabel} />
              </div>
            )}
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
