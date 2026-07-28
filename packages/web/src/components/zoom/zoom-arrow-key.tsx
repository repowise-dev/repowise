"use client";

/**
 * The one row that explains the arrows, plus the one control over them.
 *
 * Chrome goes *around* a canvas, never on it, so this is a hairline row under
 * the map rather than another floating panel. Control and key sit together
 * because they are about the same thing: a toggle whose effect is unexplained
 * is as opaque as the arrows were.
 *
 * Why one toggle and not a verb filter. Measured on a live index, the arrows
 * carry three verbs, not the seven the label vocabulary allows: `imports`
 * 80.5%, `uses`/`depends on` 14.3%, `co-changes` 5.3%. `calls`, `inherits
 * from`, `implements` and `references` cannot occur at all, because the zoom
 * map is fed file-level edges and those four are symbol-level. On top of that,
 * 89% of boxes carry a single verb across every one of their relations, so a
 * general filter would do nothing on nine boxes in ten and its main visible
 * effect would be arrows vanishing.
 *
 * Co-changes is the exception worth a control: files that change together
 * without importing each other, which is coupling no other view on the site
 * surfaces, and which is invisible here by default because it is drowned by
 * the imports it shares a canvas with.
 */

import { CO_CHANGES } from "@repowise-dev/ui/zoom";

interface ZoomArrowKeyProps {
  /** Null = every relation draws; CO_CHANGES = only co-change relations. */
  verb: string | null;
  onVerbChange: (verb: string | null) => void;
  /** Co-change relations in the whole map, so the toggle can carry its figure. */
  coChangeCount: number;
}

export function ZoomArrowKey({ verb, onVerbChange, coChangeCount }: ZoomArrowKeyProps) {
  const only = verb === CO_CHANGES;

  return (
    <div className="mt-3 flex flex-col gap-2 border-t border-[var(--color-border-default)] pt-3 sm:flex-row sm:items-center sm:justify-between">
      <p className="min-w-0 text-[13px] leading-relaxed text-[var(--color-text-secondary)]">
        {only ? (
          <>
            Showing <span className="text-[var(--color-text-primary)]">co-changes only</span>: files
            that change in the same commits without importing each other.
          </>
        ) : (
          <>
            Hover a card to see what it depends on. Arrow weight is how many file pairs the
            dependency covers.
          </>
        )}
      </p>
      {/* A count on the control tells you whether it is worth a click before
          you spend one. Only shown because it is already in the loaded map. */}
      <button
        type="button"
        onClick={() => onVerbChange(only ? null : CO_CHANGES)}
        aria-pressed={only}
        disabled={coChangeCount === 0}
        className={`shrink-0 self-start rounded-md border px-2.5 py-1 text-[12px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 sm:self-auto ${
          only
            ? "border-[var(--color-accent-primary)] text-[var(--color-accent-primary)]"
            : "border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-wash-hover)] hover:text-[var(--color-text-primary)]"
        }`}
      >
        Co-changes only{" "}
        <span className="font-mono tabular-nums text-[var(--color-text-tertiary)]">
          {coChangeCount.toLocaleString()}
        </span>
      </button>
    </div>
  );
}
