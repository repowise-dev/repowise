"use client";

import * as React from "react";
import type { StatsPunchCard } from "@repowise-dev/types/stats";
import { DEFAULT_WEEKEND_PRESET, weekendShare } from "./weekend";
import { repoArchetype } from "./archetype";
import { parseDate } from "../lib/format";

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
/** Height of the hour-total histogram under the lattice. */
const HIST_HEIGHT = 30;
/** Width of the longest weekday-total bar in the right margin. */
const DAY_BAR_WIDTH = 44;

function hourLabel(h: number): string {
  const period = h < 12 ? "AM" : "PM";
  const twelve = h % 12 === 0 ? 12 : h % 12;
  return `${twelve} ${period}`;
}

function weekdayLong(i: number): string {
  return ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][i] ?? "";
}

/** "Mar 2026 to Jul 2026", collapsing to one label when both land in a month. */
function spanLabel(first: string | null, last: string | null): string | null {
  if (!first || !last) return null;
  const fmt = (iso: string) => {
    const d = parseDate(iso);
    return Number.isNaN(d.getTime())
      ? null
      : // UTC deliberately: this is a coarse month label on an absolute instant,
        // and rendering it in the viewer's zone can slip it across a boundary.
        d.toLocaleDateString(undefined, { month: "short", year: "numeric", timeZone: "UTC" });
  };
  const a = fmt(first);
  const b = fmt(last);
  if (!a || !b) return null;
  return a === b ? a : `${a} to ${b}`;
}

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

/**
 * Coding-rhythm heatmap: commit volume by weekday x hour.
 *
 * The page's signature view and its focal point, since nothing else in the app
 * shows temporal shape at all. Presented as an open figure rather than inside a
 * card: the heading, the readout and the lattice are one object, and a border
 * around them only competes with the grid's own structure.
 *
 * Three deliberate choices about how it reads.
 *
 * Cells ramp on ink and only the single hottest cell keeps the accent. Painting
 * every active cell in the brand colour made the figure read as a slab of
 * orange rather than as a chart with a point.
 *
 * The marginal totals are drawn rather than left to the reader. Splitting a few
 * hundred commits across 168 cells caps every visible number in the low tens,
 * which makes an active repo look idle. The hour and weekday totals are where
 * the real magnitudes live, so they get shown.
 *
 * The lattice is fluid and the leftover width becomes a rail of real content. A
 * fixed-width grid inside a wide container only leaves a dead gutter.
 *
 * The clock is whatever the server resolved. `author_local` means each commit
 * was shifted by its own author's UTC offset, which is the only version of this
 * chart that means anything across timezones. The footer always names which,
 * because a heatmap that silently changes clocks is worse than one that admits
 * it has not got the data yet.
 */
export function PunchCard({
  data,
  weekendDays = DEFAULT_WEEKEND_PRESET.days,
  firstCommitAt = null,
  lastCommitAt = null,
}: {
  data: StatsPunchCard;
  /** Weekday indices (0 = Monday) counted as the weekend. */
  weekendDays?: readonly number[];
  /** Endpoints of the history, used only for the scale line in the header. */
  firstCommitAt?: string | null;
  lastCommitAt?: string | null;
}) {
  const [hover, setHover] = React.useState<{ wd: number; hr: number; count: number } | null>(null);

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
      archetype: repoArchetype(matrix, weekendDays),
    };
  }, [matrix, weekendDays]);

  if (!data || data.total === 0 || !data.peak || !derived) return null;

  const max = data.peak.count || 1;
  const weekendPct = weekendShare(data.matrix, weekendDays);
  const isLocal = data.timezone_mode === "author_local";
  const span = spanLabel(firstCommitAt, lastCommitAt);
  const { hourTotals, dayTotals, hourMax, dayMax, quiet, archetype } = derived;

  const readout = hover
    ? `${weekdayLong(hover.wd)} · ${hourLabel(hover.hr)} · ${hover.count} commit${
        hover.count === 1 ? "" : "s"
      } · ${Math.round((hover.count / data.total) * 1000) / 10}% of all work`
    : data.busiest_weekday != null && data.peak_hour != null
      ? `Most active on ${weekdayLong(data.busiest_weekday)}s around ${hourLabel(data.peak_hour)}`
      : "Commit activity by weekday and hour";

  return (
    // Capped rather than full-bleed. The lattice is fluid so it never leaves a
    // gutter, but 24 columns across a wide page inflate the cells to the point
    // where the figure dominates everything under it.
    <section
      aria-label="Coding rhythm"
      className="flex max-w-[840px] flex-col gap-4 lg:flex-row lg:gap-7"
    >
      <div className="flex min-w-0 flex-1 flex-col gap-4">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <h3 className="text-base font-semibold text-[var(--color-text-primary)]">Coding rhythm</h3>
          <span className="font-mono text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
            <b className="font-medium text-[var(--color-text-secondary)]">
              {data.total.toLocaleString()}
            </b>{" "}
            commits{span ? ` · ${span}` : ""} ·{" "}
            <b className="font-medium text-[var(--color-text-secondary)]">{weekendPct}%</b> on
            weekends
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
          <div className="min-w-[380px]" onMouseLeave={() => setHover(null)}>
            <div>
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
                      className="grid min-w-0 flex-1 grid-cols-[repeat(24,minmax(0,1fr))]"
                      style={{ gap: CELL_GAP }}
                    >
                      {Array.from({ length: 24 }, (_, hr) => {
                        const count = data.matrix[wd]?.[hr] ?? 0;
                        // sqrt keeps low-but-nonzero hours legible against the peak.
                        const intensity = count > 0 ? Math.sqrt(count / max) : 0;
                        const isPeak = count > 0 && count === max;
                        const isHover = hover?.wd === wd && hover?.hr === hr;
                        const dimmed = hover && !isHover && hover.wd !== wd && hover.hr !== hr;
                        return (
                          <div
                            key={hr}
                            onMouseEnter={() => setHover({ wd, hr, count })}
                            className={`aspect-square rounded-[2px] transition-all duration-100 ${
                              isHover ? "scale-[1.35]" : ""
                            }`}
                            style={{
                              background: isPeak
                                ? "var(--color-accent-primary)"
                                : count > 0
                                  ? "var(--color-text-primary)"
                                  : "var(--color-bg-inset)",
                              opacity: isHover
                                ? 1
                                : count > 0
                                  ? (dimmed ? 0.45 : 1) * (isPeak ? 1 : 0.12 + 0.72 * intensity)
                                  : dimmed
                                    ? 0.4
                                    : 1,
                            }}
                          />
                        );
                      })}
                    </div>

                    {/* This weekday's total, inside its own row so it can never
                        drift out of alignment with the cells it sums. */}
                    <div className="flex w-[68px] shrink-0 items-center gap-1.5">
                      <div
                        className="h-1.5 rounded-[1px]"
                        style={{
                          width: `${Math.max(dayTotals[wd]! > 0 ? 2 : 0, Math.round((dayTotals[wd]! / dayMax) * DAY_BAR_WIDTH))}px`,
                          background:
                            dayTotals[wd] === dayMax
                              ? "var(--color-accent-primary)"
                              : "var(--color-text-primary)",
                          opacity: dayTotals[wd] === dayMax ? 1 : 0.28,
                        }}
                      />
                      <span
                        className={`font-mono text-[10px] tabular-nums ${
                          dayTotals[wd] === dayMax
                            ? "font-semibold text-[var(--color-accent-primary)]"
                            : "text-[var(--color-text-tertiary)]"
                        }`}
                      >
                        {dayTotals[wd]}
                      </span>
                    </div>
                  </div>
                ))}
              </div>

              {/* Hour totals. The magnitudes the lattice cannot show. */}
              <div className="mt-1.5 flex items-end gap-2">
                <span className="w-8 shrink-0" />
                <div
                  className="grid min-w-0 flex-1 grid-cols-[repeat(24,minmax(0,1fr))] items-end"
                  style={{ gap: CELL_GAP, height: `${HIST_HEIGHT}px` }}
                >
                  {hourTotals.map((v, hr) => (
                    <div
                      key={hr}
                      className="w-full rounded-[1px]"
                      style={{
                        height: `${Math.max(v > 0 ? 1 : 0, Math.round((v / hourMax) * HIST_HEIGHT))}px`,
                        background:
                          v === hourMax
                            ? "var(--color-accent-primary)"
                            : "var(--color-text-primary)",
                        opacity: v === hourMax ? 1 : 0.28,
                      }}
                    />
                  ))}
                </div>
                {/* Mirrors the weekday-total column so the 24 tracks below the
                    lattice line up with the 24 tracks inside it. */}
                <span className="w-[68px] shrink-0" />
              </div>

              {/* Hour axis, sharing the lattice's 24 tracks. */}
              <div className="mt-1.5 flex items-center gap-2">
                <span className="w-8 shrink-0" />
                <div className="relative grid min-w-0 flex-1 grid-cols-[repeat(24,minmax(0,1fr))]">
                  {HOUR_TICKS.map(([h, label]) => (
                    <span
                      key={h}
                      className="col-span-4 whitespace-nowrap font-mono text-[10px] tabular-nums text-[var(--color-text-tertiary)]"
                      style={{ gridColumnStart: h + 1 }}
                    >
                      {label}
                    </span>
                  ))}
                </div>
                <span className="w-[68px] shrink-0" />
              </div>
            </div>
          </div>
        </div>

        <div className="flex items-center justify-between gap-2 border-t border-[var(--color-border-default)] pt-3 text-[10px] text-[var(--color-text-tertiary)]">
          <span
            title={
              isLocal
                ? "Each commit is placed at its author's own local time, using the UTC offset git recorded with it."
                : // Describes the state, prescribes nothing. This renders in the
                  // CLI's web UI and in the hosted app, where a viewer of someone
                  // else's public snapshot can act on neither a shell command nor
                  // a re-index. Also says what the rule actually is: the branch
                  // is coverage-based (_LOCAL_TIME_COVERAGE), so an index whose
                  // offsets are merely patchy lands here too, and any promised
                  // remedy would be false for it.
                  "Too few commits carry the UTC offset git records with them, so hours fall back to UTC rather than mixing two clocks in one matrix."
            }
            className="cursor-help font-mono uppercase tracking-[0.1em]"
          >
            {isLocal ? "Author-local time" : "Hours in UTC"}
          </span>
          <div className="flex items-center gap-1.5">
            <span>Less</span>
            {[0.12, 0.32, 0.55, 0.84].map((o) => (
              <span
                key={o}
                className="h-2.5 w-2.5 rounded-[2px]"
                style={{ background: "var(--color-text-primary)", opacity: o }}
              />
            ))}
            <span
              className="h-2.5 w-2.5 rounded-[2px]"
              style={{ background: "var(--color-accent-primary)" }}
            />
            <span>Peak</span>
          </div>
        </div>
      </div>

      {/* The rail. Fills width that a fixed lattice would waste, with readings
          worth having rather than padding. Drops below the chart when narrow. */}
      <aside className="flex shrink-0 flex-col gap-5 border-t border-[var(--color-border-default)] pt-4 lg:w-48 lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0">
        {archetype && (
          <div className="flex flex-col gap-1">
            <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
              Ships like a
            </span>
            <span className="text-xl font-semibold leading-tight text-[var(--color-accent-primary)]">
              {archetype.name}
            </span>
            <span className="text-[11px] leading-snug text-[var(--color-text-tertiary)]">
              {archetype.because}
            </span>
          </div>
        )}

        {quiet && (
          <div className="flex flex-col gap-1.5">
            <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
              Quietest stretch
            </span>
            <p className="text-[13px] leading-snug text-[var(--color-text-secondary)]">
              No commit has ever landed between{" "}
              <span className="font-mono">{hourLabel(quiet.from)}</span> and{" "}
              <span className="font-mono">{hourLabel((quiet.to + 1) % 24)}</span>.
            </p>
          </div>
        )}

        <div className="flex flex-col gap-1.5">
          <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
            Hottest slot
          </span>
          <p className="text-[13px] leading-snug text-[var(--color-text-secondary)]">
            <span className="font-mono">
              {weekdayLong(data.peak.weekday).slice(0, 3)} {hourLabel(data.peak.hour)}
            </span>
            , with {data.peak.count} commits. The single busiest hour in this repo&apos;s life.
          </p>
        </div>
      </aside>
    </section>
  );
}
