import { Briefcase, CalendarHeart, Clock, Moon, Sun, Sunrise, Sunset } from "lucide-react";
import type { StatsChronotype } from "@repowise-dev/types/stats";
import { formatNumber } from "../lib/format";
import { DEFAULT_WEEKEND_PRESET } from "./weekend";
import { contributorArchetype, NAME_MIN_COMMITS } from "./archetype";

/** Icon and colour per earned name. Anything unlisted reads as neutral. */
const BADGES: Record<string, { icon: typeof Moon; color: string }> = {
  "Night Owl": { icon: Moon, color: "var(--color-info)" },
  "Dawn Patrol": { icon: Sunrise, color: "var(--color-warning)" },
  "Weekend Warrior": { icon: CalendarHeart, color: "var(--color-accent-primary)" },
  "Nine to Fiver": { icon: Briefcase, color: "var(--color-text-tertiary)" },
  Clockwork: { icon: Clock, color: "var(--color-info)" },
  "The Closer": { icon: Sunset, color: "var(--color-warning)" },
  "Afternoon Regular": { icon: Sun, color: "var(--color-text-tertiary)" },
  Daylight: { icon: Sun, color: "var(--color-text-tertiary)" },
};

/** Fallback for people under the naming floor, or payloads without histograms. */
const PLAIN: Record<StatsChronotype["label"], string> = {
  night_owl: "Night owl",
  early_bird: "Early bird",
  daylight: "Daylight",
};

function hourLabel(h: number): string {
  const period = h < 12 ? "am" : "pm";
  const twelve = h % 12 === 0 ? 12 : h % 12;
  return `${twelve}${period}`;
}

/**
 * When each frequent contributor actually commits, and what that makes them.
 *
 * Only rendered when the index carries per-commit UTC offsets. In UTC mode the
 * "night owl" award would just go to whoever lives furthest east, so the server
 * withholds the data entirely rather than let the UI publish a timezone
 * artifact as a personality trait.
 *
 * Names are earned by threshold and carry their evidence in the tooltip. Under
 * `NAME_MIN_COMMITS` nobody gets one: a habit needs enough commits to be a
 * habit, and the plain chronotype still says something true.
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
        <h3 className="text-base font-semibold text-[var(--color-text-primary)]">
          How everyone ships
        </h3>
        <span className="font-mono text-[11px] uppercase tracking-[0.1em] text-[var(--color-text-tertiary)]">
          Each person&apos;s local time
        </span>
      </div>

      <ul className="flex flex-col divide-y divide-[var(--color-border-default)] border-y border-[var(--color-border-default)]">
        {people.map((p) => {
          const earned = contributorArchetype(p, weekendDays);
          const badge = earned ? BADGES[earned.name] : undefined;
          const Icon = badge?.icon ?? Sun;
          const color = badge?.color ?? "var(--color-text-tertiary)";
          const text = earned?.name ?? PLAIN[p.label] ?? PLAIN.daylight;
          const why = earned
            ? `${earned.because}, over ${formatNumber(p.commits)} commits.`
            : `Names are only awarded above ${NAME_MIN_COMMITS} commits. ${p.night_pct}% of their commits land between 10pm and 5am.`;
          return (
            <li key={p.name} className="flex items-center gap-4 py-3">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-[var(--color-text-primary)]">
                  {p.name}
                </p>
                <p className="text-xs tabular-nums text-[var(--color-text-tertiary)]">
                  {formatNumber(p.commits)} commits · peaks around {hourLabel(p.peak_hour)}
                </p>
              </div>
              <span
                className="flex shrink-0 cursor-help items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium"
                style={{ color, borderColor: color }}
                title={why}
              >
                <Icon className="h-3.5 w-3.5" />
                {text}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
