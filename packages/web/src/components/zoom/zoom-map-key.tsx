"use client";

/**
 * The row that says what the map's marks mean, plus the one control over them.
 *
 * Chrome goes *around* a canvas, never on it, so this is a hairline row under
 * the map rather than another floating panel. It carries the two things a card
 * shows without naming: the accent dot in its top-right corner and the health
 * dot in its footer. Before this, a reader had no way to learn either one, and
 * the two dots shared a palette, so working out one taught you the wrong thing
 * about the other.
 *
 * Why one toggle and not a verb filter. Measured on a live index, the arrows
 * carry three verbs, not the seven the label vocabulary allows: `imports`
 * 80.5%, `uses` 14.3%, `co-changes` 5.3%. `calls`, `inherits from`,
 * `implements` and `references` cannot occur at all, because the zoom map is
 * fed file-level edges and those four are symbol-level. On top of that, 89% of
 * boxes carry a single verb across every one of their relations, so a general
 * filter would do nothing on nine boxes in ten and its main visible effect
 * would be arrows vanishing.
 *
 * Co-changes is the exception worth a control: files that change together
 * without importing each other, which is coupling no other view on the site
 * surfaces, and which is invisible here by default because it is drowned by
 * the imports it shares a canvas with.
 */

import { CO_CHANGES } from "@repowise-dev/ui/zoom";
import { HEALTH_BAND_LABEL } from "@repowise-dev/types/health";

interface ZoomMapKeyProps {
  /** Null = every relation draws; CO_CHANGES = only co-change relations. */
  verb: string | null;
  onVerbChange: (verb: string | null) => void;
  /** Co-change relations in the whole map, so the toggle can carry its figure. */
  coChangeCount: number;
}

function Dot({ className }: { className: string }) {
  return <span aria-hidden className={`inline-block h-2 w-2 shrink-0 rounded-full ${className}`} />;
}

export function ZoomMapKey({ verb, onVerbChange, coChangeCount }: ZoomMapKeyProps) {
  const only = verb === CO_CHANGES;

  return (
    <div className="mt-3 border-t border-[var(--color-border-default)] pt-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <p className="min-w-0 text-[13px] leading-relaxed text-[var(--color-text-secondary)]">
          {only ? (
            <>
              Showing <span className="text-[var(--color-text-primary)]">co-changes only</span>:
              files that change in the same commits without importing each other.
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

      {/* The dots. Health keeps the traffic light because the colours carry a
          band; the role dot is one accent hue meaning "there is something
          here", and the hover card names which. */}
      <dl className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-1 text-[12px] text-[var(--color-text-tertiary)]">
        <div className="flex items-center gap-1.5">
          <Dot className="bg-[var(--color-accent-primary)]" />
          <dt className="sr-only">Top-right dot</dt>
          <dd>Entry point, hotspot, dead code or on an execution flow. Hover for which.</dd>
        </div>
        <div className="flex items-center gap-1.5">
          <Dot className="bg-[var(--color-success)]" />
          <Dot className="bg-[var(--color-warning)]" />
          <Dot className="bg-[var(--color-error)]" />
          <dt className="sr-only">Footer dot</dt>
          <dd>
            Code health: {HEALTH_BAND_LABEL.healthy} 8+, {HEALTH_BAND_LABEL.warning} 4 to 8,{" "}
            {HEALTH_BAND_LABEL.alert} under 4.
          </dd>
        </div>
      </dl>
    </div>
  );
}
