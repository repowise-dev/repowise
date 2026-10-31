"use client";

import { cn } from "../lib/cn";
import type { AreaCouplingCopy } from "./area-copy";
import type { CouplingAreaFilters } from "./area-view-state";

const SUPPORT_STEPS = [0, 3, 5, 10];
const RECENCY_STEPS = [90, 365];
const DEPTHS = [1, 2, 3, 4];

const CONTROL =
  "h-8 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2 text-xs text-[var(--color-text-primary)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-focus-ring)]";

function Toggle({
  label,
  pressed,
  onChange,
}: {
  label: string;
  pressed: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={() => onChange(!pressed)}
      className={cn(
        CONTROL,
        "px-2.5",
        pressed
          ? "bg-[var(--color-bg-selected)] font-medium shadow-[inset_0_-2px_0_var(--color-tab-active)]"
          : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]",
      )}
    >
      {label}
    </button>
  );
}

/** *options*, plus *value* when a shared link carries one the list does not offer. */
function withCurrent(
  options: { value: string; label: string }[],
  value: string,
  label: (value: string) => string,
): { value: string; label: string }[] {
  return options.some((o) => o.value === value) ? options : [...options, { value, label: label(value) }];
}

function Select({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
}) {
  return (
    <label className="inline-flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
      {label}
      <select value={value} onChange={(e) => onChange(e.target.value)} className={CONTROL}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export interface CouplingAreaFiltersBarProps {
  value: CouplingAreaFilters;
  onChange: (next: CouplingAreaFilters) => void;
  /** The depth the server chose, named on the "Auto" option. */
  resolvedDepth: number;
  copy: AreaCouplingCopy["filters"];
}

/** The four filters and the grouping depth, one row that wraps on a phone. */
export function CouplingAreaFiltersBar({
  value,
  onChange,
  resolvedDepth,
  copy,
}: CouplingAreaFiltersBarProps) {
  const set = (patch: Partial<CouplingAreaFilters>) => onChange({ ...value, ...patch });
  return (
    <div role="group" aria-label={copy.label} className="flex flex-wrap items-center gap-2">
      <Toggle label={copy.unexplained} pressed={value.unexplained} onChange={(v) => set({ unexplained: v })} />
      <Toggle label={copy.crossArea} pressed={value.crossArea} onChange={(v) => set({ crossArea: v })} />
      <Toggle label={copy.includeTests} pressed={value.includeTests} onChange={(v) => set({ includeTests: v })} />
      <Select
        label={copy.minSupport}
        value={String(value.minSupport)}
        options={withCurrent(
          SUPPORT_STEPS.map((n) => ({
            value: String(n),
            label: n === 0 ? copy.anySupport : copy.atLeast(n),
          })),
          String(value.minSupport),
          (v) => copy.atLeast(Number(v)),
        )}
        onChange={(v) => set({ minSupport: Number(v) })}
      />
      <Select
        label={copy.recency}
        value={String(value.recentDays ?? "")}
        options={withCurrent(
          [
            { value: "", label: copy.anyTime },
            ...RECENCY_STEPS.map((d) => ({ value: String(d), label: copy.lastDays(d) })),
          ],
          String(value.recentDays ?? ""),
          (v) => copy.lastDays(Number(v)),
        )}
        onChange={(v) => set({ recentDays: v ? Number(v) : null })}
      />
      <Select
        label={copy.depth}
        value={String(value.depth ?? "")}
        options={withCurrent(
          [
            { value: "", label: copy.autoDepth(resolvedDepth) },
            ...DEPTHS.map((d) => ({ value: String(d), label: String(d) })),
          ],
          String(value.depth ?? ""),
          (v) => v,
        )}
        onChange={(v) => set({ depth: v ? Number(v) : null })}
      />
    </div>
  );
}
