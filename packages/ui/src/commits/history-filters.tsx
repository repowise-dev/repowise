"use client";

import { useEffect, useState } from "react";
import { Flame } from "lucide-react";
import { Input } from "../ui/input";
import { Segmented } from "../shared/segmented";
import { cn } from "../lib/cn";

/** What a commit or change did to health, as the quick filter names it. */
export type HealthImpact = "all" | "better" | "worse" | "none";
export type HistoryWindow = "week" | "month" | "quarter" | "all" | "custom";

export interface HistoryFilterValue {
  impact: HealthImpact;
  hotspots: boolean;
  window: HistoryWindow;
  /** `YYYY-MM-DD`, read only for the custom window; `until` is inclusive. */
  since?: string | undefined;
  until?: string | undefined;
  author?: string | undefined;
  area?: string | undefined;
}

export interface HistoryFilterLabels {
  impactLabel: string;
  all: string;
  better: string;
  worse: string;
  none: string;
  hotspots: string;
  windowLabel: string;
  week: string;
  month: string;
  quarter: string;
  allTime: string;
  custom: string;
  from: string;
  to: string;
  author: string;
  area: string;
}

const DEFAULT_LABELS: HistoryFilterLabels = {
  impactLabel: "Health impact",
  all: "All",
  better: "Made things better",
  worse: "Made things worse",
  none: "No health impact",
  hotspots: "Touched hotspots",
  windowLabel: "Time window",
  week: "Past week",
  month: "Past month",
  quarter: "Past quarter",
  allTime: "All time",
  custom: "Custom",
  from: "From",
  to: "To",
  author: "Author email or name",
  area: "Area, e.g. packages/core",
};

export interface HistoryFiltersProps {
  value: HistoryFilterValue;
  onChange: (patch: Partial<HistoryFilterValue>) => void;
  /** Per-impact counts under the other filters, so a selected impact never
   *  hides its alternatives' sizes. */
  counts?: Record<HealthImpact, number> | undefined;
  labels?: Partial<HistoryFilterLabels> | undefined;
  className?: string | undefined;
}

/**
 * The quick filters over a history list: health impact first, then hotspots,
 * the time window, author and area. Controlled and server-driven: the host
 * writes each change to the URL and refetches, so the counts and the list
 * always describe the same set.
 */
export function HistoryFilters({ value, onChange, counts, labels, className }: HistoryFiltersProps) {
  const l = { ...DEFAULT_LABELS, ...labels };
  const count = (k: HealthImpact) => counts?.[k]?.toLocaleString();
  return (
    <div className={cn("flex flex-col gap-2.5", className)}>
      <div className="flex flex-wrap items-center gap-2">
        <Segmented<HealthImpact>
          label={l.impactLabel}
          value={value.impact}
          options={[
            { value: "all", label: l.all, count: count("all") },
            { value: "better", label: l.better, count: count("better") },
            { value: "worse", label: l.worse, count: count("worse") },
            { value: "none", label: l.none, count: count("none") },
          ]}
          onChange={(impact) => onChange({ impact })}
        />
        <button
          type="button"
          aria-pressed={value.hotspots}
          onClick={() => onChange({ hotspots: !value.hotspots })}
          className={cn(
            "inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium transition-colors",
            value.hotspots
              ? "border-[var(--color-accent-primary)] text-[var(--color-text-primary)]"
              : "border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]",
          )}
        >
          <Flame aria-hidden className="h-3.5 w-3.5" />
          {l.hotspots}
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Segmented<HistoryWindow>
          label={l.windowLabel}
          value={value.window}
          options={[
            { value: "week", label: l.week },
            { value: "month", label: l.month },
            { value: "quarter", label: l.quarter },
            { value: "all", label: l.allTime },
            { value: "custom", label: l.custom },
          ]}
          onChange={(window) => onChange({ window })}
        />
        {value.window === "custom" && (
          <>
            <DateField label={l.from} value={value.since} onCommit={(since) => onChange({ since })} />
            <DateField label={l.to} value={value.until} onCommit={(until) => onChange({ until })} />
          </>
        )}
        <TextField label={l.author} value={value.author} onCommit={(author) => onChange({ author })} />
        <TextField label={l.area} value={value.area} onCommit={(area) => onChange({ area })} mono />
      </div>
    </div>
  );
}

/** A text filter that applies on Enter or blur, not per keystroke: each
 *  apply is a server round trip. */
function TextField({
  label,
  value,
  onCommit,
  mono,
}: {
  label: string;
  value: string | undefined;
  onCommit: (next: string | undefined) => void;
  mono?: boolean;
}) {
  const [draft, setDraft] = useState(value ?? "");
  useEffect(() => setDraft(value ?? ""), [value]);
  const commit = () => {
    const next = draft.trim() || undefined;
    if (next !== value) onCommit(next);
  };
  return (
    <Input
      type="search"
      aria-label={label}
      placeholder={label}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => e.key === "Enter" && commit()}
      className={cn("h-8 w-full text-xs sm:w-52", mono && "font-mono")}
    />
  );
}

function DateField({
  label,
  value,
  onCommit,
}: {
  label: string;
  value: string | undefined;
  onCommit: (next: string | undefined) => void;
}) {
  return (
    <label className="inline-flex items-center gap-1.5 text-xs text-[var(--color-text-tertiary)]">
      {label}
      <Input
        type="date"
        value={value ?? ""}
        onChange={(e) => onCommit(e.target.value || undefined)}
        className="h-8 w-36 text-xs"
      />
    </label>
  );
}
