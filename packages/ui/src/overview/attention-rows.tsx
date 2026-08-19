import * as React from "react";
import type { AttentionItem } from "../dashboard/attention-href";

/** Same item the panel renders. Deliberately not a parallel local type: the
 *  two surfaces must resolve identical targets, and a looser `type: string`
 *  here would silently accept a value `getDefaultHref` cannot route. */
export type AttentionRowItem = AttentionItem;

const TYPE_LABEL: Record<string, string> = {
  stale_decision: "Stale decision",
  proposed_decision: "Proposed",
  ungoverned_hotspot: "Ungoverned",
  knowledge_silo: "Knowledge silo",
  dead_code: "Dead code",
};

const SEVERITY_COLOR: Record<string, string> = {
  high: "var(--color-error)",
  medium: "var(--color-warning)",
  low: "var(--color-text-tertiary)",
};

/**
 * Triage items as full-width rows.
 *
 * Replaces a 320px rail panel whose titles truncated mid-word — "Cap additional
 * same-family single-byte encodings after defin…" is a layout bug reported as
 * content. Given the whole column, the title fits and the item is readable
 * without a hover.
 *
 * Kept deliberately plain: what belongs in this list is a content question
 * (README.md and a marketplace manifest are not worth a person's attention),
 * and that is being worked separately. This component only fixes the shape.
 */
export function AttentionRows({
  items,
  hrefFor,
  LinkComponent,
}: {
  items: AttentionRowItem[];
  /** Resolves an item to its target page; null renders the row unlinked. */
  hrefFor: (item: AttentionRowItem) => string | null;
  LinkComponent?: React.ElementType;
}) {
  const A = LinkComponent ?? "a";
  if (items.length === 0) {
    return (
      <p className="text-xs text-[var(--color-success)]">
        Nothing needs attention right now.
      </p>
    );
  }

  return (
    <ul className="m-0 list-none divide-y divide-[var(--color-border-default)] border-t border-[var(--color-border-default)] p-0">
      {items.map((item) => {
        const href = hrefFor(item);
        const body = (
          <>
            <span className="flex shrink-0 items-center gap-1.5 sm:w-32">
              <span
                aria-hidden
                className="h-1.5 w-1.5 shrink-0 rounded-full"
                style={{ background: SEVERITY_COLOR[item.severity] ?? "var(--color-text-tertiary)" }}
              />
              {/* Severity is carried by the dot's colour, which is nothing at
                  all to a screen reader (and to anyone who cannot separate the
                  three hues). The text equivalent goes here rather than into a
                  `title`, which assistive tech reads inconsistently. */}
              <span className="sr-only">{item.severity} severity. </span>
              <span className="font-mono text-[9.5px] uppercase tracking-[0.1em] text-[var(--color-text-tertiary)]">
                {TYPE_LABEL[item.type] ?? item.type}
              </span>
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-[var(--color-text-primary)] [text-wrap:pretty]">
                {item.title}
              </span>
              <span className="mt-0.5 block text-[11px] text-[var(--color-text-tertiary)] [text-wrap:pretty]">
                {item.description}
              </span>
            </span>
          </>
        );
        return (
          <li key={item.id}>
            {href ? (
              <A
                href={href}
                className="group flex flex-col gap-1 py-2.5 text-xs no-underline sm:flex-row sm:items-baseline sm:gap-3"
              >
                {body}
              </A>
            ) : (
              <div className="flex flex-col gap-1 py-2.5 text-xs sm:flex-row sm:items-baseline sm:gap-3">
                {body}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
