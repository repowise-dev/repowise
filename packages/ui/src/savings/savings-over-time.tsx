"use client";

import * as React from "react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Segmented } from "../shared/segmented";
import { formatCost, formatDate, formatNumber, formatTokens } from "../lib/format";
import type { SavingsCopy } from "./copy";
import type { SavingsView } from "./types";

export type SavingsLens = "usd" | "tokens" | "files";

const HEIGHT = 200;

interface Point {
  day: string;
  value: number;
}

/** The series one lens draws, oldest first, straight off the report. */
function series(data: SavingsView, lens: SavingsLens): Point[] {
  if (lens === "files") {
    return (data.files_per_day ?? [])
      .filter((row) => row.group)
      .map((row) => ({ day: row.group as string, value: row.files_not_opened ?? 0 }));
  }
  return (data.per_day ?? [])
    .filter((row) => row.group)
    .map((row) => ({
      day: row.group as string,
      value: lens === "usd" ? row.priced_input_savings_usd ?? 0 : row.saved_input_tokens ?? 0,
    }));
}

const FORMAT: Record<SavingsLens, (v: number) => string> = {
  usd: formatCost,
  tokens: formatTokens,
  files: formatNumber,
};

/**
 * Savings per day under one lens. A neutral line; the accent marks only the
 * latest point, which is the one a reader looks at first. A lens with nothing
 * in this range is disabled with its reason rather than drawn flat.
 */
export function SavingsOverTime({
  data,
  copy,
  title,
}: {
  data: SavingsView;
  copy: SavingsCopy;
  title: string;
}) {
  const priced = (data.priced_input_savings_usd ?? 0) > 0;
  const hasFiles = (data.files_not_opened ?? 0) > 0;
  const [lens, setLens] = React.useState<SavingsLens>(priced ? "usd" : "tokens");
  const active: SavingsLens = (lens === "usd" && !priced) || (lens === "files" && !hasFiles) ? "tokens" : lens;
  const points = React.useMemo(() => series(data, active), [data, active]);
  const fmt = FORMAT[active];
  const filesStart = active === "files" ? points.find((p) => p.value > 0)?.day : undefined;

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2.5 border-b border-[var(--color-border-default)] pb-3">
        <h2 className="text-[18px] font-semibold text-[var(--color-text-primary)]">{title}</h2>
        <Segmented<SavingsLens>
          label={copy.lensLabel}
          value={active}
          onChange={setLens}
          options={[
            { value: "usd", label: copy.lens.usd, disabledReason: priced ? undefined : copy.lensUsdOff },
            { value: "tokens", label: copy.lens.tokens },
            { value: "files", label: copy.lens.files, disabledReason: hasFiles ? undefined : copy.lensFilesOff },
          ]}
        />
      </div>
      {points.length < 2 ? (
        <p className="text-[15px] text-[var(--color-text-secondary)]">{copy.tooFewDays}</p>
      ) : (
        <div style={{ height: HEIGHT }} role="img" aria-label={copy.chartAria(copy.lens[active], points.length)}>
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={points} margin={{ top: 8, right: 24, bottom: 0, left: 0 }}>
              <XAxis
                dataKey="day"
                tick={{ fill: "var(--color-text-tertiary)", fontSize: 10, fontFamily: "var(--font-mono)" }}
                axisLine={false}
                tickLine={false}
                interval="preserveStartEnd"
                minTickGap={32}
                tickFormatter={shortDay}
              />
              <YAxis
                width={52}
                tick={{ fill: "var(--color-text-tertiary)", fontSize: 10, fontFamily: "var(--font-mono)" }}
                axisLine={false}
                tickLine={false}
                tickFormatter={(v: number) => fmt(v)}
              />
              <Tooltip
                cursor={{ stroke: "var(--color-border-default)" }}
                contentStyle={{
                  background: "var(--color-bg-overlay)",
                  border: "1px solid var(--color-border-default)",
                  borderRadius: "6px",
                  fontSize: "12px",
                  color: "var(--color-text-primary)",
                }}
                formatter={(value) => [fmt(Number(value)), copy.lens[active]]}
                labelFormatter={(label) => formatDate(String(label))}
              />
              <Area
                type="monotone"
                dataKey="value"
                stroke="var(--color-text-secondary)"
                strokeWidth={1.5}
                fill="var(--color-text-secondary)"
                fillOpacity={0.1}
                isAnimationActive={false}
                dot={(props: { cx?: number; cy?: number; index?: number }) =>
                  props.index === points.length - 1 && props.cx != null && props.cy != null ? (
                    <circle key="latest" cx={props.cx} cy={props.cy} r={3.5} fill="var(--color-accent-primary)" />
                  ) : (
                    <g key={props.index} />
                  )
                }
                activeDot={{ r: 3.5, fill: "var(--color-text-primary)", stroke: "none" }}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
      {filesStart && <p className="text-xs text-[var(--color-text-tertiary)]">{copy.filesSince(formatDate(filesStart))}</p>}
    </section>
  );
}

/** `2026-09-19` reads as `Sep 19` on a crowded axis. */
function shortDay(value: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!m) return value;
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(
    new Date(value),
  );
}
