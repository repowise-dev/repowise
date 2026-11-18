"use client";

import * as React from "react";
import type { StatsPunchCard } from "@repowise-dev/types/stats";
import { DEFAULT_WEEKEND_PRESET, weekendShare } from "./weekend";
import { formatNumber } from "../lib/format";
import { hourLabel, wholePct } from "./numbers";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;
const WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
// Axis ticks at the quarter-day marks.
const HOUR_TICKS: Array<[number, string]> = [
  [0, "12am"],
  [6, "6am"],
  [12, "12pm"],
  [18, "6pm"],
];
/** Height of the hour-total histogram under the lattice. */
const HIST_HEIGHT = 30;
/** Width of the longest weekday-total bar in the right margin. */
const DAY_BAR_WIDTH = 22;

/**
 * Longest unbroken run of hours nobody has ever committed in.
 *
 * Wraps around midnight, because on most repos the quiet stretch straddles it.
 * Returns null when every hour of the day has seen at least one commit, which
 * is a fact worth not overstating as silence.
 */
function quietStretch(hours: number[]): { from: number; to: number } | null {
  if (hours.every((n) => n > 0)) return null;
  let best: { from: number; to: number; len: number } | null = null;
  let start: number | null = null;
  let len = 0;
  // Two laps so a run spanning midnight is seen whole.
  for (let i = 0; i < 48; i += 1) {
    const h = i % 24;
    if (hours[h] === 0) {
      if (start === null) start = h;
      len += 1;
      if (len <= 24 && (!best || len > best.len)) best = { from: start, to: h, len };
    } else {
      start = null;
      len = 0;
    }
  }
  return best && best.len >= 2 ? { from: best.from, to: best.to } : null;
}

type Cell = { wd: number; hr: number };

/**
 * Below this many commits an empty run of hours is as likely chance as habit,
 * so "Quiet hours" stays off. Ceiling: a flat count, blind to how the commits
 * spread; a per-hour expected-count test would be the upgrade.
 */
export const QUIET_MIN_COMMITS = 200;

/** Weekday indices the matrix can hold (0 = Monday .. 6 = Sunday); anything else is dropped. */
function validWeekend(days: readonly number[]): readonly number[] {
  const ok = days.filter((d) => Number.isInteger(d) && d >= 0 && d <= 6);
  return ok.length > 0 ? ok : DEFAULT_WEEKEND_PRESET.days;
}

const ARROWS: Record<string, [number, number]> = {
  ArrowLeft: [0, -1],
  ArrowRight: [0, 1],
  ArrowUp: [-1, 0],
  ArrowDown: [1, 0],
};

const strong = "font-medium text-[var(--color-text-primary)]";

function RailItem({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5 border-t border-[var(--color-border-default)] pt-4 first:border-t-0 first:pt-0">
      <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        {label}
      </span>
      <p className="text-[15px] leading-snug text-[var(--color-text-secondary)]">{children}</p>
    </div>
  );
}

/**
 * Coding-rhythm heatmap: commit volume by weekday x hour, with both marginal
 * totals drawn and a rail of readings beside it.
 *
 * Cells ramp on ink and only the single hottest cell keeps the accent. The
 * marginal totals are drawn because splitting a few hundred commits across 168
 * cells caps every visible number in the low tens; the hour and weekday totals
 * are where the real magnitudes live, and they stay visible without hover.
 *
 * The lattice is fluid down to phone width, so the page never scrolls
 * sideways. A cell's exact count shows on hover, on tap (which pins it), and
 * from the keyboard: the lattice takes focus and the arrow keys walk it.
 *
 * The clock is whatever the server resolved. `author_local` means each commit
 * was shifted by its own author's UTC offset. The footer says which in visible
 * text rather than a tooltip, so touch readers see it too.
 */
export function PunchCard({
  data,
  weekendDays = DEFAULT_WEEKEND_PRESET.days,
  scope,
  partialCommits,
}: {
  data: StatsPunchCard;
  /** Weekday indices (0 = Monday) counted as the weekend. */
  weekendDays?: readonly number[];
  /** How much history the matrix was drawn from, shown under the chart. */
  scope?: string | undefined;
  /** Set when the matrix covers only the latest N commits, not the whole history. */
  partialCommits?: number | undefined;
}) {
  const [hover, setHover] = React.useState<Cell | null>(null);
  const [pinned, setPinned] = React.useState<Cell | null>(null);

  const matrix = data?.matrix;
  const derived = React.useMemo(() => {
    if (!matrix || matrix.length !== 7) return null;
    const hourTotals = Array.from({ length: 24 }, (_, h) =>
      matrix.reduce((a, row) => a + (row[h] ?? 0), 0),
    );
    const dayTotals = matrix.map((row) => row.reduce((a, n) => a + n, 0));
    return {
      hourTotals,
      dayTotals,
      hourMax: Math.max(...hourTotals, 1),
      dayMax: Math.max(...dayTotals, 1),
      quiet: quietStretch(hourTotals),
    };
  }, [matrix]);

  if (!data || data.total === 0 || !data.peak || !derived) return null;

  const peak = data.peak;
  const max = peak.count || 1;
  const weekend = validWeekend(weekendDays);
  const weekendPct = Math.round(weekendShare(data.matrix, weekend));
  const weekendNames = weekend.map((d) => WEEKDAY_NAMES[d]).join(" and ");
  const isLocal = data.timezone_mode === "author_local";
  const { hourTotals, dayTotals, hourMax, dayMax } = derived;
  const quiet = data.total >= QUIET_MIN_COMMITS ? derived.quiet : null;
  const busiestDay = dayTotals.indexOf(dayMax);
  const active = hover ?? pinned;

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      setPinned(null);
      return;
    }
    const d = ARROWS[e.key];
    if (!d) return;
    e.preventDefault();
    const from = active ?? { wd: peak.weekday, hr: peak.hour };
    setHover(null);
    setPinned({ wd: (from.wd + d[0] + 7) % 7, hr: (from.hr + d[1] + 24) % 24 });
  };

  let readout: React.ReactNode;
  if (active) {
    const c = data.matrix[active.wd]?.[active.hr] ?? 0;
    readout = (
      <>
        <b className={strong}>
          {WEEKDAY_NAMES[active.wd]}, {hourLabel(active.hr)}
        </b>
        : {formatNumber(c)} commit{c === 1 ? "" : "s"}, {wholePct(c, data.total)}% of all work.
      </>
    );
  } else {
    readout = (
      <>
        Busiest hour:{" "}
        <b className={strong}>
          {WEEKDAY_NAMES[peak.weekday]}, {hourLabel(peak.hour)}
        </b>
        , {formatNumber(peak.count)} commits. Tap or point at any hour to read it.
      </>
    );
  }

  return (
    <section aria-label="Coding rhythm" className="grid grid-cols-1 lg:grid-cols-12">
      <div className="flex min-w-0 flex-col gap-4 pb-6 lg:col-span-8 lg:pb-0 lg:pr-6">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <h2 className="text-lg font-semibold text-[var(--color-text-primary)]">
            When the work happens
          </h2>
          <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
            {isLocal ? "Each author's local time" : "Hours in UTC"}
          </span>
        </div>

        <div
          tabIndex={0}
          role="group"
          aria-label="Commits by weekday and hour. Use the arrow keys to read each hour."
          onKeyDown={onKeyDown}
          onBlur={(e) => {
            // Focus leaving the grid drops the pinned hour, so neither the
            // outline nor the readout sticks once the reader has moved on.
            if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setPinned(null);
          }}
          onMouseLeave={() => setHover(null)}
          className="rounded-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[var(--color-focus-ring)]"
        >
          <div className="flex flex-col gap-[2px] sm:gap-[3px]">
            {WEEKDAYS.map((day, wd) => (
              <div key={day} className="flex items-center gap-1.5 sm:gap-2">
                <span className="w-7 shrink-0 text-right font-mono text-[10px] uppercase text-[var(--color-text-tertiary)]">
                  {day}
                </span>
                <div className="grid min-w-0 flex-1 grid-cols-[repeat(24,minmax(0,1fr))] gap-[2px] sm:gap-[3px]">
                  {Array.from({ length: 24 }, (_, hr) => {
                    const count = data.matrix[wd]?.[hr] ?? 0;
                    // sqrt keeps low-but-nonzero hours legible against the peak.
                    const intensity = count > 0 ? Math.sqrt(count / max) : 0;
                    const isPeak = wd === peak.weekday && hr === peak.hour;
                    const isActive = active?.wd === wd && active?.hr === hr;
                    return (
                      <div
                        key={hr}
                        data-cell={`${wd}-${hr}`}
                        onMouseEnter={() => setHover({ wd, hr })}
                        onClick={() => setPinned({ wd, hr })}
                        className={`aspect-square cursor-pointer rounded-[2px] ${
                          isActive
                            ? "outline outline-2 outline-offset-1 outline-[var(--color-text-primary)]"
                            : ""
                        }`}
                        style={{
                          background: isPeak
                            ? "var(--color-accent-primary)"
                            : count > 0
                              ? "var(--color-text-primary)"
                              : "var(--color-bg-inset)",
                          opacity: isPeak || count === 0 ? 1 : 0.12 + 0.72 * intensity,
                        }}
                      />
                    );
                  })}
                </div>
                {/* This weekday's total, inside its own row so it can never
                    drift out of alignment with the cells it sums. */}
                <div className="flex w-12 shrink-0 items-center gap-1 sm:w-14">
                  <div
                    className="h-1 shrink-0 rounded-[1px] bg-[var(--color-text-primary)] opacity-30"
                    style={{
                      width: `${Math.max(dayTotals[wd]! > 0 ? 2 : 0, Math.round((dayTotals[wd]! / dayMax) * DAY_BAR_WIDTH))}px`,
                    }}
                  />
                  <span className="font-mono text-[10px] tabular-nums text-[var(--color-text-tertiary)]">
                    {formatNumber(dayTotals[wd]!)}
                  </span>
                </div>
              </div>
            ))}
          </div>

          {/* Hour totals: the magnitudes the lattice cannot show. */}
          <div className="mt-1.5 flex items-end gap-1.5 sm:gap-2">
            <span className="w-7 shrink-0" />
            <div
              className="grid min-w-0 flex-1 grid-cols-[repeat(24,minmax(0,1fr))] items-end gap-[2px] sm:gap-[3px]"
              style={{ height: `${HIST_HEIGHT}px` }}
            >
              {hourTotals.map((v, hr) => (
                <div
                  key={hr}
                  className="w-full rounded-t-[1px] bg-[var(--color-text-primary)] opacity-30"
                  style={{
                    height: `${Math.max(v > 0 ? 1 : 0, Math.round((v / hourMax) * HIST_HEIGHT))}px`,
                  }}
                />
              ))}
            </div>
            <span className="w-12 shrink-0 sm:w-14" />
          </div>
          <div className="mt-1.5 flex items-center gap-1.5 sm:gap-2">
            <span className="w-7 shrink-0" />
            <div className="grid min-w-0 flex-1 grid-cols-[repeat(24,minmax(0,1fr))]">
              {HOUR_TICKS.map(([h, label]) => (
                <span
                  key={h}
                  className="col-span-4 whitespace-nowrap font-mono text-[10px] text-[var(--color-text-tertiary)]"
                  style={{ gridColumnStart: h + 1 }}
                >
                  {label}
                </span>
              ))}
            </div>
            <span className="w-12 shrink-0 sm:w-14" />
          </div>
        </div>

        {/* Holds its height so the lattice never reflows as the reading changes. */}
        <p
          className="min-h-[2.75rem] text-[15px] text-[var(--color-text-secondary)] sm:min-h-[1.5rem]"
          aria-live="polite"
        >
          {readout}
        </p>

        <p className="border-t border-[var(--color-border-default)] pt-3 text-xs text-[var(--color-text-tertiary)]">
          {scope ? `${scope} ` : ""}
          {isLocal
            ? "Each commit sits at its author's own local time, from the UTC offset git recorded with it."
            : "Too few commits carry a UTC offset, so hours are shown in UTC rather than mixing two clocks."}
        </p>
      </div>

      <aside className="flex flex-col gap-4 border-t border-[var(--color-border-default)] pt-6 lg:col-span-4 lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0">
        <RailItem label="Weekend share">
          <b className={strong}>{weekendPct}%</b> of commits land on {weekendNames}.
        </RailItem>
        {quiet && (
          <RailItem label="Quiet hours">
            {partialCommits
              ? `None of the latest ${formatNumber(partialCommits)} commits`
              : "No commit has ever"}{" "}
            landed between <b className={strong}>{hourLabel(quiet.from)}</b> and{" "}
            <b className={strong}>{hourLabel((quiet.to + 1) % 24)}</b>.
          </RailItem>
        )}
        <RailItem label="Busiest weekday">
          <b className={strong}>{WEEKDAY_NAMES[busiestDay]}</b>, with {formatNumber(dayMax)} commits.
        </RailItem>
      </aside>
    </section>
  );
}
