"use client";

import * as React from "react";
import type { StatsPunchCard } from "@repowise-dev/types/stats";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;
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
 * Coding-rhythm heatmap: a GitHub-punch-card of commit volume by weekday x hour.
 * The signature "when does this team actually code" view — nothing else on the
 * stats page shows temporal shape. Cells ramp the accent color by a sqrt-scaled
 * intensity, and hovering a cell reads out its weekday/hour/count live.
 */
export function PunchCard({ data }: { data: StatsPunchCard }) {
  const [hover, setHover] = React.useState<{ wd: number; hr: number; count: number } | null>(null);

  if (!data || data.total === 0 || !data.peak) return null;

  const max = data.peak.count || 1;
  const readout = hover
    ? `${weekdayLong(hover.wd)} · ${hourLabel(hover.hr)} · ${hover.count} commit${
        hover.count === 1 ? "" : "s"
      }`
    : data.busiest_weekday != null && data.peak_hour != null
      ? `Most active on ${weekdayLong(data.busiest_weekday)}s around ${hourLabel(data.peak_hour)}`
      : "Commit activity by weekday and hour";

  return (
    <Card className="h-full">
      <CardHeader className="flex-row items-center justify-between gap-3 pb-3">
        <CardTitle className="text-sm">Coding rhythm</CardTitle>
        <span className="shrink-0 rounded-full bg-[var(--color-bg-muted)] px-2.5 py-1 text-[11px] font-medium tabular-nums text-[var(--color-text-secondary)]">
          {data.weekend_pct}% on weekends
        </span>
      </CardHeader>
      <CardContent className="pt-0">
        <p
          className={`mb-3 text-xs transition-colors ${
            hover
              ? "font-medium text-[var(--color-text-primary)]"
              : "text-[var(--color-text-secondary)]"
          }`}
        >
          {readout}
        </p>

        <div className="overflow-x-auto">
          <div className="min-w-[420px]" onMouseLeave={() => setHover(null)}>
            {WEEKDAYS.map((day, wd) => (
              <div key={day} className="flex items-center gap-1.5">
                <span
                  className={`w-8 shrink-0 py-px text-right text-[10px] font-medium uppercase tracking-wide transition-colors ${
                    hover?.wd === wd
                      ? "text-[var(--color-accent-primary)]"
                      : "text-[var(--color-text-tertiary)]"
                  }`}
                >
                  {day}
                </span>
                <div className="grid flex-1 grid-cols-[repeat(24,minmax(0,1fr))] gap-[3px]">
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
                          isHover
                            ? "scale-[1.35] ring-1 ring-[var(--color-accent-primary)]"
                            : ""
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

            {/* Hour axis — ticks aligned to the 24-column grid. */}
            <div className="mt-1.5 flex items-center gap-1.5">
              <span className="w-8 shrink-0" />
              <div className="relative grid flex-1 grid-cols-[repeat(24,minmax(0,1fr))]">
                {HOUR_TICKS.map(([h, label]) => (
                  <span
                    key={h}
                    className="col-span-1 whitespace-nowrap text-[10px] tabular-nums text-[var(--color-text-tertiary)]"
                    style={{ gridColumnStart: h + 1 }}
                  >
                    {label}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* Legend — hours are bucketed in UTC (git records the commit instant,
            not the author's local offset), so we say so rather than imply local
            time. */}
        <div className="mt-3 flex items-center justify-between gap-2 text-[10px] text-[var(--color-text-tertiary)]">
          <span>Hours in UTC</span>
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
      </CardContent>
    </Card>
  );
}
