"use client";

/**
 * The row that says what the map's marks mean, plus the controls over them.
 *
 * Chrome goes *around* a canvas, never on it, so this is a hairline row under
 * the map rather than another floating panel. It carries the two things a card
 * shows without naming: the accent dot in its top-right corner and the health
 * dot in its footer. Before this, a reader had no way to learn either one, and
 * the two dots shared a palette, so working out one taught you the wrong thing
 * about the other.
 *
 * Why a verb filter now, when a single co-changes toggle was right before. The
 * toggle was sized against a map fed file-level edges only: the arrows carried
 * three verbs, 80.5% of them `imports`, four of the label vocabulary's seven
 * "could not occur at all" because they are symbol-level, and 89% of boxes had
 * one verb across every relation. A general filter would have done nothing on
 * nine boxes in ten.
 *
 * Projecting the symbol graph onto file pairs removed that argument rather than
 * weakening it. The four missing verbs now occur: Ocelot draws all seven, and
 * the share of boxes carrying more than one verb goes 48% -> 67% there and
 * 8% -> 32% on jhipster-sample-app. "Show me what calls what, not what imports
 * what" is a question the map can now answer, so the control earns its place.
 *
 * No verbs selected means every verb draws, rather than an empty canvas. A
 * filter whose natural resting state hides everything trains people not to
 * touch it.
 */

import { FilterChip } from "@repowise-dev/ui/health";
import { type VerbCount, toggleVerb } from "@repowise-dev/ui/zoom";
import { HEALTH_BAND_LABEL } from "@repowise-dev/types/health";

interface ZoomMapKeyProps {
  /** Every verb present in the loaded map, descending by count. */
  verbs: VerbCount[];
  /** Files placed in the tree, and files curation left in no layer. The second
   *  is the map's missing denominator: without it, a map short one file in
   *  seven reads as complete. */
  totalFiles: number;
  unclaimedFiles: number;
  /** Null = every relation draws; otherwise only these verbs do. */
  selected: ReadonlySet<string> | null;
  onSelectedChange: (verbs: ReadonlySet<string> | null) => void;
}

function Dot({ className }: { className: string }) {
  return <span aria-hidden className={`inline-block h-2 w-2 shrink-0 rounded-full ${className}`} />;
}

export function ZoomMapKey({
  verbs,
  totalFiles,
  unclaimedFiles,
  selected,
  onSelectedChange,
}: ZoomMapKeyProps) {
  const filtered = selected !== null;

  return (
    <div className="mt-3 border-t border-[var(--color-border-default)] pt-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-baseline sm:justify-between">
        <p className="min-w-0 text-[13px] leading-relaxed text-[var(--color-text-secondary)]">
          {filtered ? (
            <>
              Showing{" "}
              <span className="text-[var(--color-text-primary)]">
                {[...selected].join(", ")}
              </span>{" "}
              only. Arrow weight is how many file pairs the relation covers.
            </>
          ) : (
            <>
              Hover a card to see what it depends on. Arrow weight is how many file pairs the
              dependency covers.
            </>
          )}
          {unclaimedFiles > 0 && (
            /* Silence when nothing is missing: "0 not shown" is noise. */
            <>
              {" "}
              {unclaimedFiles.toLocaleString()} of{" "}
              {(totalFiles + unclaimedFiles).toLocaleString()} files are not on the map; they
              belong to no layer.
            </>
          )}
        </p>
        {verbs.length > 1 && (
          /* Counts sit on the chips so you can tell whether one is worth a
             click before you spend one. Already in the loaded map. */
          <div className="flex flex-wrap items-center gap-1.5">
            {verbs.map(({ verb, count }) => (
              <FilterChip
                key={verb}
                active={selected?.has(verb) ?? false}
                onClick={() => onSelectedChange(toggleVerb(selected, verb))}
              >
                {verb}{" "}
                <span className="font-mono tabular-nums text-[var(--color-text-tertiary)]">
                  {count.toLocaleString()}
                </span>
              </FilterChip>
            ))}
            {filtered && (
              <button
                type="button"
                onClick={() => onSelectedChange(null)}
                className="rounded-md px-2 py-1 text-xs text-[var(--color-text-secondary)] underline-offset-2 transition-colors hover:text-[var(--color-text-primary)] hover:underline"
              >
                Clear
              </button>
            )}
          </div>
        )}
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
