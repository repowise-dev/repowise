"use client";

import * as React from "react";
import type { StatsPunchCard } from "@repowise-dev/types/stats";
import { DEFAULT_WEEKEND_PRESET, weekendShare } from "./weekend";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;
/** One gap value on both axes, so the cells read as an even lattice. */
const CELL_GAP = "3px";
// Axis ticks at the quarter-day marks, labelled in the reader's am/pm idiom.
const HOUR_TICKS: Array<[number, string]> = [
  [0, "12a"],
  [6, "6a"],
  [12, "12p"],
  [18, "6p"],
];

function hourLabel(h: number): string {
  const period = h < 12 ? "AM" : "PM";
  const twelve = h % 12 === 0 ? 12 : h % 12;
  return `${twelve} ${period}`;
}

function weekdayLong(i: number): string {
  return ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][i] ?? "";
}

/**
 * Coding-rhythm heatmap: commit volume by weekday x hour.
 *
 * The page's signature view and its focal point — nothing else in the app shows
 * temporal shape at all. Presented as an open figure rather than inside a card:
 * the heading, the live readout and the lattice are one object, and a border
 * around them only competes with the grid's own structure.
 *
 * Cells ramp the accent by a sqrt-scaled intensity so low-but-nonzero hours stay
 * legible against the peak; hovering reads out the exact cell and dims the rest.
 *
 * The clock is whatever the server resolved: `author_local` means each commit
 * was shifted by its own author's UTC offset, which is the only version of this
 * chart that means anything across timezones. The footer always names which,
 * because a heatmap that silently changes clocks is worse than one that admits
 * it hasn't got the data yet.
 */
export function PunchCard({
  data,
  weekendDays = DEFAULT_WEEKEND_PRESET.days,
}: {
  data: StatsPunchCard;
  /** Weekday indices (0 = Monday) counted as the weekend. */
  weekendDays?: readonly number[];
}) {
  const [hover, setHover] = React.useState<{ wd: number; hr: number; count: number } | null>(null);

  if (!data || data.total === 0 || !data.peak) return null;

  const max = data.peak.count || 1;
  const weekendPct = weekendShare(data.matrix, weekendDays);
  const isLocal = data.timezone_mode === "author_local";

  const readout = hover
    ? `${weekdayLong(hover.wd)} · ${hourLabel(hover.hr)} · ${hover.count} commit${
        hover.count === 1 ? "" : "s"
      }`
    : data.busiest_weekday != null && data.peak_hour != null
      ? `Most active on ${weekdayLong(data.busiest_weekday)}s around ${hourLabel(data.peak_hour)}`
      : "Commit activity by weekday and hour";

  return (
    <section aria-label="Coding rhythm" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className="text-base font-semibold text-[var(--color-text-primary)]">Coding rhythm</h3>
        <span className="font-mono text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
          {weekendPct}% on weekends
        </span>
      </div>

      {/* The readout is the chart's title line, so it holds its height rather
          than reflowing the lattice every time the pointer moves. */}
      <p
        className={`min-h-[1.25rem] text-sm transition-colors ${
          hover
            ? "font-medium text-[var(--color-text-primary)]"
            : "text-[var(--color-text-secondary)]"
        }`}
        aria-live="polite"
      >
        {readout}
      </p>

      <div className="overflow-x-auto">
        <div className="min-w-[420px]" onMouseLeave={() => setHover(null)}>
          <div className="flex flex-col" style={{ gap: CELL_GAP }}>
            {WEEKDAYS.map((day, wd) => (
              <div key={day} className="flex items-center gap-2">
                <span
                  className={`w-8 shrink-0 py-px text-right font-mono text-[10px] uppercase tracking-wide transition-colors ${
                    hover?.wd === wd
                      ? "text-[var(--color-accent-primary)]"
                      : "text-[var(--color-text-tertiary)]"
                  }`}
                >
                  {day}
                </span>
                <div
                  className="grid flex-1 grid-cols-[repeat(24,minmax(0,1fr))]"
                  style={{ gap: CELL_GAP }}
                >
                  {Array.from({ length: 24 }, (_, hr) => {
                    const count = data.matrix[wd]?.[hr] ?? 0;
                    // sqrt keeps low-but-nonzero hours legible against the peak.
                    const intensity = count > 0 ? Math.sqrt(count / max) : 0;
                    const isHover = hover?.wd === wd && hover?.hr === hr;
                    const dimmed = hover && !isHover && hover.wd !== wd && hover.hr !== hr;
                    return (
                      <div
                        key={hr}
                        onMouseEnter={() => setHover({ wd, hr, count })}
                        className={`aspect-square rounded-[2px] transition-all duration-100 ${
                          isHover ? "scale-[1.35] ring-1 ring-[var(--color-accent-primary)]" : ""
                        }`}
                        style={{
                          background:
                            count > 0 ? "var(--color-accent-primary)" : "var(--color-bg-muted)",
                          opacity: isHover
                            ? 1
                            : count > 0
                              ? (dimmed ? 0.5 : 1) * (0.16 + 0.84 * intensity)
                              : dimmed
                                ? 0.4
                                : 1,
                        }}
                      />
                    );
                  })}
                </div>
              </div>
            ))}
          </div>

          {/* Hour axis — ticks aligned to the 24-column grid. */}
          <div className="mt-2 flex items-center gap-2">
            <span className="w-8 shrink-0" />
            <div className="relative grid flex-1 grid-cols-[repeat(24,minmax(0,1fr))]">
              {HOUR_TICKS.map(([h, label]) => (
                <span
                  key={h}
                  className="col-span-1 whitespace-nowrap font-mono text-[10px] tabular-nums text-[var(--color-text-tertiary)]"
                  style={{ gridColumnStart: h + 1 }}
                >
                  {label}
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="flex items-center justify-between gap-2 border-t border-[var(--color-border-default)] pt-3 text-[10px] text-[var(--color-text-tertiary)]">
        <span
          title={
            isLocal
              ? "Each commit is placed at its author's own local time, using the UTC offset git recorded with it."
              : "This index predates the commit-offset capture, so hours fall back to UTC. Run `repowise update` to switch to author-local time."
          }
          className="cursor-help font-mono uppercase tracking-[0.1em]"
        >
          {isLocal ? "Author-local time" : "Hours in UTC"}
        </span>
        <div className="flex items-center gap-1.5">
          <span>Less</span>
          {[0.16, 0.44, 0.72, 1].map((o) => (
            <span
              key={o}
              className="h-2.5 w-2.5 rounded-[2px]"
              style={{ background: "var(--color-accent-primary)", opacity: o }}
            />
          ))}
          <span>More</span>
        </div>
      </div>
    </section>
  );
}
