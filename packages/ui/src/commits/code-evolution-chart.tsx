"use client";

import { useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { GitBranch, Info } from "lucide-react";
import type {
  CommitCategory,
  CommitEvolution,
} from "@repowise-dev/types/git";
import {
  Tooltip as UiTooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../ui/tooltip";
import { cn } from "../lib/cn";

/**
 * The repo's development "story arc" — a stacked-area timeline of how the
 * commit mix (feature / fix / refactor / docs / …) shifts over its history.
 * Each commit is classified into exactly one category from its subject, so the
 * bands stack cleanly; the share/volume toggle flips between proportion and
 * raw throughput. The narrative line up top reads the arc for the user (e.g.
 * "feature-led early, fix-and-docs heavy lately").
 */

interface CategoryMeta {
  label: string;
  color: string;
  blurb: string;
}

// Drawn bottom-to-top in this order; "feature" anchors the base so the growth
// band reads as the foundation the rest of the work sits on. Colours route
// through semantic design tokens (theme-aware, no raw hex) and are picked so
// the mapping reads intuitively: green = new capability, red = fixes, etc.
const CATEGORY_META: Record<CommitCategory, CategoryMeta> = {
  feature: { label: "Feature", color: "var(--color-success)", blurb: "new capability" },
  fix: { label: "Fix", color: "var(--color-error)", blurb: "bug / regression" },
  refactor: { label: "Refactor", color: "var(--color-accent-primary)", blurb: "reshape / perf" },
  docs: { label: "Docs", color: "var(--color-info)", blurb: "documentation" },
  test: { label: "Test", color: "var(--color-amber)", blurb: "tests / coverage" },
  deps: { label: "Deps", color: "var(--color-caution)", blurb: "dependency bumps" },
  chore: { label: "Chore", color: "var(--color-text-tertiary)", blurb: "tooling / CI / release" },
  other: { label: "Other", color: "var(--color-border-active)", blurb: "unclassified" },
};

const STACK_ORDER: CommitCategory[] = [
  "feature",
  "fix",
  "refactor",
  "docs",
  "test",
  "deps",
  "chore",
  "other",
];

type Mode = "share" | "volume";

function formatBucketLabel(startIso: string, granularity: string): string {
  const d = new Date(startIso);
  if (Number.isNaN(d.getTime())) return startIso;
  const month = d.toLocaleString("en-US", { month: "short" });
  if (granularity === "week") {
    return `${month} ${d.getDate()}`;
  }
  return `${month} ${String(d.getFullYear()).slice(2)}`;
}

/** Dominant category across a slice of buckets (by summed count). */
function dominant(
  buckets: CommitEvolution["buckets"],
  cats: CommitCategory[],
): CommitCategory | null {
  const sums = new Map<CommitCategory, number>();
  for (const b of buckets) {
    for (const c of cats) sums.set(c, (sums.get(c) ?? 0) + (b.counts[c] ?? 0));
  }
  let best: CommitCategory | null = null;
  let bestN = 0;
  for (const [c, n] of sums) {
    if (n > bestN) {
      best = c;
      bestN = n;
    }
  }
  return best;
}

function buildNarrative(data: CommitEvolution): string | null {
  const { buckets, categories } = data;
  if (buckets.length < 3 || categories.length === 0) return null;
  const slice = Math.max(1, Math.floor(buckets.length / 4));
  const early = dominant(buckets.slice(0, slice), categories);
  const late = dominant(buckets.slice(-slice), categories);
  if (!early || !late) return null;
  if (early === late) {
    return `Consistently ${CATEGORY_META[early].label.toLowerCase()}-driven across its history.`;
  }
  return `Began ${CATEGORY_META[early].label.toLowerCase()}-led, now leaning ${CATEGORY_META[late].label.toLowerCase()}.`;
}

interface TooltipPayloadItem {
  dataKey: CommitCategory;
  value: number;
  payload: { _label: string; _total: number };
}

function ChartTooltip({
  active,
  payload,
  mode,
}: {
  active?: boolean;
  payload?: TooltipPayloadItem[];
  mode: Mode;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const total = payload[0]?.payload?._total ?? 0;
  const rows = payload
    .filter((p) => (p.value ?? 0) > 0)
    .sort((a, b) => b.value - a.value);
  return (
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] px-3 py-2 text-xs shadow-md">
      <div className="mb-1.5 flex items-baseline justify-between gap-4">
        <span className="font-medium text-[var(--color-text-primary)]">
          {payload[0]?.payload?._label}
        </span>
        <span className="tabular-nums text-[var(--color-text-tertiary)]">
          {total} commit{total === 1 ? "" : "s"}
        </span>
      </div>
      <div className="space-y-1">
        {rows.map((p) => {
          const meta = CATEGORY_META[p.dataKey];
          const pct = total > 0 ? Math.round((p.value / total) * 100) : 0;
          return (
            <div key={p.dataKey} className="flex items-center gap-2">
              <span
                className="h-2 w-2 shrink-0 rounded-[2px]"
                style={{ background: meta.color }}
              />
              <span className="text-[var(--color-text-secondary)]">{meta.label}</span>
              <span className="ml-auto tabular-nums text-[var(--color-text-primary)]">
                {mode === "share" ? `${pct}%` : p.value}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function CodeEvolutionChart({
  evolution,
  className,
}: {
  evolution: CommitEvolution;
  className?: string;
}) {
  const [mode, setMode] = useState<Mode>("share");

  const cats = useMemo(
    () => STACK_ORDER.filter((c) => evolution.categories.includes(c)),
    [evolution.categories],
  );

  const chartData = useMemo(() => {
    return evolution.buckets.map((b) => {
      const row: Record<string, number | string> = {
        _label: formatBucketLabel(b.start, evolution.granularity),
        _total: b.total,
      };
      for (const c of cats) {
        const raw = b.counts[c] ?? 0;
        row[c] =
          mode === "share" && b.total > 0
            ? Math.round((raw / b.total) * 1000) / 10
            : raw;
      }
      return row;
    });
  }, [evolution.buckets, evolution.granularity, cats, mode]);

  const narrative = useMemo(() => buildNarrative(evolution), [evolution]);
  const grandTotal = evolution.total_commits || 1;

  if (evolution.buckets.length === 0) return null;

  return (
    <div
      className={cn(
        "rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4 sm:p-5",
        className,
      )}
    >
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <GitBranch className="h-4 w-4 text-[var(--color-accent-primary)]" />
            <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
              Code Evolution
            </h3>
            <TooltipProvider delayDuration={150}>
              <UiTooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    aria-label="How categories are derived"
                    className="text-[var(--color-text-tertiary)] transition-colors hover:text-[var(--color-text-secondary)]"
                  >
                    <Info className="h-3.5 w-3.5" />
                  </button>
                </TooltipTrigger>
                <TooltipContent className="max-w-[260px] text-xs leading-relaxed">
                  Every indexed commit is classified into one category from its
                  subject (conventional-commit prefix when present, else
                  keywords). Bands stack to 100% in share mode.
                </TooltipContent>
              </UiTooltip>
            </TooltipProvider>
          </div>
          <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
            {narrative ??
              "How this repo's commit mix shifts over time."}
          </p>
        </div>

        <div className="flex items-center rounded-lg border border-[var(--color-border-default)] p-0.5 text-xs">
          {(["share", "volume"] as Mode[]).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              className={cn(
                "rounded-md px-2.5 py-1 font-medium capitalize transition-colors",
                mode === m
                  ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
                  : "text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]",
              )}
            >
              {m}
            </button>
          ))}
        </div>
      </div>

      <ResponsiveContainer width="100%" height={260}>
        <AreaChart
          data={chartData}
          margin={{ top: 4, right: 8, bottom: 0, left: -16 }}
        >
          <CartesianGrid
            strokeDasharray="3 3"
            vertical={false}
            stroke="var(--color-border-subtle, var(--color-border-default))"
            opacity={0.4}
          />
          <XAxis
            dataKey="_label"
            tick={{ fontSize: 10, fill: "var(--color-text-tertiary)" }}
            axisLine={false}
            tickLine={false}
            minTickGap={24}
          />
          <YAxis
            tick={{ fontSize: 10, fill: "var(--color-text-tertiary)" }}
            axisLine={false}
            tickLine={false}
            width={36}
            domain={mode === "share" ? [0, 100] : [0, "auto"]}
            tickFormatter={(v) => (mode === "share" ? `${v}%` : `${v}`)}
          />
          <Tooltip
            content={<ChartTooltip mode={mode} />}
            cursor={{ stroke: "var(--color-text-tertiary)", strokeWidth: 1, strokeDasharray: "3 3" }}
          />
          {cats.map((c) => (
            <Area
              key={c}
              type="monotone"
              dataKey={c}
              stackId="1"
              stroke={CATEGORY_META[c].color}
              strokeWidth={1}
              fill={CATEGORY_META[c].color}
              fillOpacity={0.7}
              isAnimationActive={false}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>

      {/* Legend doubles as a window-wide breakdown: each category's lifetime share. */}
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5">
        {cats.map((c) => {
          const n = evolution.totals[c] ?? 0;
          const pct = Math.round((n / grandTotal) * 100);
          return (
            <div key={c} className="flex items-center gap-1.5 text-xs">
              <span
                className="h-2.5 w-2.5 shrink-0 rounded-[3px]"
                style={{ background: CATEGORY_META[c].color }}
              />
              <span className="text-[var(--color-text-secondary)]">
                {CATEGORY_META[c].label}
              </span>
              <span className="tabular-nums text-[var(--color-text-tertiary)]">
                {pct}%
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
