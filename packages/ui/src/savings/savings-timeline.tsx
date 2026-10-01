"use client";

import * as React from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";

import { formatTokens } from "../lib/format";
import type { SavingsBreakdownRow } from "./types";

/** Below this many days with a recorded saving, a bar chart is two or three
 *  bars in a wide empty box: the shape carries nothing a sentence does not.
 *  The design language says to measure the distribution before drawing it, so
 *  the component measures it and declines. */
export const MIN_DAYS_FOR_CHART = 4;

export const TIMELINE_HEIGHT = 220;

export interface SavingsTimelineProps {
  /** The report's `per_day` series. */
  days: SavingsBreakdownRow[];
  height?: number;
}

/** The day rows worth drawing: named days that actually recorded a saving. */
function plottable(days: SavingsBreakdownRow[]): Array<{ day: string; tokens: number }> {
  return days
    .filter((d) => d.group !== null && d.saved_input_tokens > 0)
    .map((d) => ({ day: d.group as string, tokens: d.saved_input_tokens }))
    .sort((a, b) => a.day.localeCompare(b.day));
}

/**
 * Savings over time, when there is enough of it to have a shape.
 *
 * Returns a sentence instead of a chart under {@link MIN_DAYS_FOR_CHART}, and
 * renders a textual summary beside the chart in every case. A canvas without
 * an equivalent reading is unreachable for anyone not looking at it, and this
 * one's important result -- that savings accrue, and roughly how evenly -- is
 * a sentence anyway.
 */
export function SavingsTimeline({ days, height = TIMELINE_HEIGHT }: SavingsTimelineProps) {
  const data = React.useMemo(() => plottable(days), [days]);

  if (data.length === 0) {
    return (
      <p className="text-sm text-[var(--color-text-secondary)]">
        No day in this window recorded a saving.
      </p>
    );
  }

  const first = data[0] as { day: string; tokens: number };
  const last = data[data.length - 1] as { day: string; tokens: number };
  const busiest = data.reduce((a, b) => (b.tokens > a.tokens ? b : a), first);

  // "between 2026-09-18 and 2026-09-18" is how a range reads when there is
  // only one day in it.
  const span =
    first.day === last.day ? (
      <>on {first.day}</>
    ) : (
      <>
        between {first.day} and {last.day}
      </>
    );

  const summary = (
    <p className="text-[13px] leading-relaxed text-[var(--color-text-secondary)]">
      {data.length} day{data.length === 1 ? "" : "s"} recorded a saving {span}.
      {data.length > 1 && (
        <>
          {" "}
          The largest was {formatTokens(busiest.tokens)} tokens on {busiest.day}.
        </>
      )}
    </p>
  );

  if (data.length < MIN_DAYS_FOR_CHART) {
    // Too few points for a bar chart to mean anything. The sentence above is
    // the whole result, so it is rendered on its own rather than as a caption
    // under an almost-empty plot.
    return summary;
  }

  return (
    <div className="flex flex-col gap-3">
      {/* The plot box is reserved before recharts measures it: a bare
          ResponsiveContainer renders nothing until its ResizeObserver fires,
          so the section grew into place on every mount. */}
      <div style={{ height }} role="img" aria-label={ariaLabel(data, busiest)}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <XAxis
              dataKey="day"
              tick={{ fill: "var(--color-text-tertiary)", fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              interval="preserveStartEnd"
              minTickGap={24}
              tickFormatter={shortDay}
            />
            <YAxis
              tick={{ fill: "var(--color-text-tertiary)", fontSize: 10 }}
              axisLine={false}
              tickLine={false}
              tickFormatter={(v: number) => formatTokens(v)}
            />
            <Tooltip
              cursor={{ fill: "var(--color-bg-elevated)" }}
              contentStyle={{
                background: "var(--color-bg-overlay)",
                border: "1px solid var(--color-border-default)",
                borderRadius: "6px",
                fontSize: "12px",
                color: "var(--color-text-primary)",
              }}
              formatter={(value) => [`${formatTokens(Number(value))} tokens`, "Saved"]}
              labelFormatter={(label) => String(label)}
            />
            <Bar dataKey="tokens" fill="var(--color-accent-fill)" radius={[3, 3, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      {summary}
    </div>
  );
}

/** `2026-09-19` reads as `9/19` on a crowded axis. Anything else is left
 *  alone rather than mangled. */
function shortDay(value: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  return m ? `${Number(m[2])}/${Number(m[3])}` : value;
}

function ariaLabel(
  data: Array<{ day: string; tokens: number }>,
  busiest: { day: string; tokens: number },
): string {
  return `Savings per day, ${data.length} days. Largest ${formatTokens(
    busiest.tokens,
  )} tokens on ${busiest.day}.`;
}
