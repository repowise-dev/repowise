import { Moon, Sun, Sunrise } from "lucide-react";
import type { StatsChronotype } from "@repowise-dev/types/stats";
import { formatNumber } from "../lib/format";

const LABELS: Record<
  StatsChronotype["label"],
  { text: string; icon: typeof Moon; color: string }
> = {
  night_owl: { text: "Night owl", icon: Moon, color: "var(--color-info)" },
  early_bird: { text: "Early bird", icon: Sunrise, color: "var(--color-warning)" },
  daylight: { text: "Daylight", icon: Sun, color: "var(--color-text-tertiary)" },
};

function hourLabel(h: number): string {
  const period = h < 12 ? "am" : "pm";
  const twelve = h % 12 === 0 ? 12 : h % 12;
  return `${twelve}${period}`;
}

/**
 * When each frequent contributor actually commits.
 *
 * Only rendered when the index carries per-commit UTC offsets — in UTC mode the
 * "night owl" award would just go to whoever lives furthest east, so the server
 * withholds the data entirely rather than let the UI publish a timezone
 * artifact as a personality trait.
 *
 * The bar is the person's own 24-hour histogram, normalised to their own peak,
 * so someone with 600 commits and someone with 20 are compared on shape rather
 * than volume.
 */
export function ChronotypeList({ people }: { people: StatsChronotype[] }) {
  if (!people || people.length === 0) return null;

  return (
    <section aria-label="Commit-hour habits" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className="text-base font-semibold text-[var(--color-text-primary)]">
          Night owls &amp; early birds
        </h3>
        <span className="font-mono text-[11px] uppercase tracking-[0.1em] text-[var(--color-text-tertiary)]">
          Each person&apos;s local time
        </span>
      </div>

      <ul className="flex flex-col divide-y divide-[var(--color-border-default)] border-y border-[var(--color-border-default)]">
        {people.map((p) => {
          const meta = LABELS[p.label] ?? LABELS.daylight;
          const Icon = meta.icon;
          return (
            <li key={p.name} className="flex items-center gap-4 py-3">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-[var(--color-text-primary)]">
                  {p.name}
                </p>
                <p className="text-xs text-[var(--color-text-tertiary)] tabular-nums">
                  {formatNumber(p.commits)} commits · peaks around {hourLabel(p.peak_hour)}
                </p>
              </div>
              <span
                className="flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium"
                style={{ color: meta.color, borderColor: meta.color }}
                title={`${p.night_pct}% of commits between 10pm and 5am`}
              >
                <Icon className="h-3.5 w-3.5" />
                {meta.text}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
