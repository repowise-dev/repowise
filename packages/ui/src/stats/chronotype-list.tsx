import type { StatsChronotype } from "@repowise-dev/types/stats";
import { formatNumber } from "../lib/format";
import { DEFAULT_WEEKEND_PRESET } from "./weekend";
import { contributorArchetype, NAME_MIN_COMMITS } from "./archetype";
import { hourLabel } from "./numbers";

/** Fallback for people under the naming floor, or payloads without histograms. */
const PLAIN: Record<StatsChronotype["label"], string> = {
  night_owl: "Night owl",
  early_bird: "Early bird",
  daylight: "Daylight",
};

const MICRO = "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";
const COLS = "grid grid-cols-[minmax(0,1fr)_112px] gap-x-4 sm:grid-cols-[minmax(0,1.2fr)_minmax(96px,1.6fr)_minmax(0,1fr)]";

/** One person's 24 hours as small bars, scaled to their own busiest hour. */
function DayStrip({ hours, name }: { hours: number[]; name: string }) {
  const max = Math.max(...hours, 1);
  return (
    <div className="flex h-[22px] items-end gap-px" role="img" aria-label={`${name}'s commits by hour of day`}>
      {hours.map((v, h) => (
        <div
          key={h}
          className="min-h-px flex-1 bg-[var(--color-text-primary)] opacity-50"
          style={{ height: `${Math.round((v / max) * 100)}%` }}
        />
      ))}
    </div>
  );
}

/**
 * When each frequent contributor commits, drawn as their own 24-hour strip,
 * and the habit that earns them a name.
 *
 * Only rendered when the index carries per-commit UTC offsets. In UTC mode the
 * "night owl" award would just go to whoever lives furthest east, so the
 * server withholds the data rather than publish a timezone artifact as a
 * personality trait.
 *
 * Names are earned by threshold and the reason is printed beside the name,
 * not hidden in a tooltip, so it reads on touch too. Under `NAME_MIN_COMMITS`
 * nobody gets one: a habit needs enough commits to be a habit.
 */
export function ChronotypeList({
  people,
  weekendDays = DEFAULT_WEEKEND_PRESET.days,
}: {
  people: StatsChronotype[];
  /** Weekday indices (0 = Monday) counted as the weekend. */
  weekendDays?: readonly number[];
}) {
  if (!people || people.length === 0) return null;

  return (
    <section aria-label="Commit-hour habits" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-lg font-semibold text-[var(--color-text-primary)]">Who keeps which hours</h2>
        <span className={MICRO}>
          Each person&apos;s local time · names from {NAME_MIN_COMMITS} commits
        </span>
      </div>

      <ul className="flex flex-col border-t border-[var(--color-border-default)]">
        {people.map((p) => {
          const earned = contributorArchetype(p, weekendDays);
          const hours = p.hour_commits?.length === 24 ? p.hour_commits : null;
          return (
            <li
              key={p.name}
              className={`${COLS} items-center gap-y-1 border-b border-[var(--color-border-default)] py-2.5`}
            >
              <div className="min-w-0">
                <p className="break-words text-[15px] text-[var(--color-text-primary)]">{p.name}</p>
                <p className="text-xs tabular-nums text-[var(--color-text-tertiary)]">
                  {formatNumber(p.commits)} commits
                </p>
              </div>
              <div className="row-span-2 sm:row-span-1">
                {hours && <DayStrip hours={hours} name={p.name} />}
              </div>
              <p className="text-xs leading-snug text-[var(--color-text-secondary)] sm:text-right">
                <b className="font-medium text-[var(--color-text-primary)] sm:block sm:text-[15px]">
                  {earned?.name ?? PLAIN[p.label] ?? PLAIN.daylight}
                  <span className="sm:hidden">:</span>
                </b>{" "}
                {earned ? earned.because : `peaks around ${hourLabel(p.peak_hour)}`}
              </p>
            </li>
          );
        })}
      </ul>
      <div className={`${COLS} -mt-2`} aria-hidden>
        <span />
        <div className="hidden justify-between font-mono text-[10px] text-[var(--color-text-tertiary)] sm:flex">
          <span>12am</span>
          <span>6am</span>
          <span>12pm</span>
          <span>6pm</span>
          <span>12am</span>
        </div>
      </div>
    </section>
  );
}
