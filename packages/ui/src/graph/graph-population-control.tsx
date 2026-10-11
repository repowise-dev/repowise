"use client";

import { useState } from "react";
import { ChevronDown, Filter } from "lucide-react";
import { Popover, PopoverTrigger, PopoverContent } from "../ui/popover";
import { formatNumber } from "../lib/format";
import type { GraphPopulation, PopulationBreakdown } from "@repowise-dev/types/graph";

const ROWS: { key: keyof GraphPopulation; label: string; count: keyof PopulationBreakdown }[] = [
  { key: "tests", label: "Tests", count: "tests" },
  { key: "examples", label: "Examples and benchmarks", count: "examples" },
  { key: "docs", label: "Docs and config", count: "docs" },
];

/**
 * Which files the community views count. Production is always in; the three
 * toggles add the rest. The server recomputes sizes, ranking, labels and the
 * health rollup over whatever is on, so this is a change to the data, not a
 * paint filter.
 */
export function GraphPopulationControl({
  population,
  breakdown,
  onChange,
  className,
}: {
  population: GraphPopulation;
  /** From the architecture payload; absent while it loads or in a scope that
   *  does not fetch it, in which case the rows show without counts. */
  breakdown?: PopulationBreakdown | null | undefined;
  onChange: (next: GraphPopulation) => void;
  className?: string | undefined;
}) {
  const [open, setOpen] = useState(false);
  const allOn = population.tests && population.examples && population.docs;
  const summary = breakdown
    ? allOn || breakdown.visible === breakdown.total
      ? `All ${formatNumber(breakdown.total)} files`
      : `${formatNumber(breakdown.visible)} of ${formatNumber(breakdown.total)} files`
    : allOn
      ? "All files"
      : "Production files";

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={`Files counted: ${summary}`}
          className={`inline-flex h-7 items-center gap-1.5 rounded-md border border-[var(--color-border-default)] px-2 text-xs text-[var(--color-text-secondary)] transition-colors hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] ${className ?? ""}`}
        >
          <Filter className="h-3 w-3" />
          <span className="tabular-nums">{summary}</span>
          <ChevronDown className="h-3 w-3" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" aria-label="Files counted" className="w-64 p-2">
        <p className="px-1 pb-1.5 text-[11px] text-[var(--color-text-secondary)]">
          Production code is always counted. Turning a kind on re-sizes and
          re-ranks every group.
        </p>
        {ROWS.map((row) => {
          const count = breakdown?.[row.count];
          return (
            <label
              key={row.key}
              className="flex cursor-pointer items-center justify-between gap-2 rounded px-1 py-1.5 text-xs text-[var(--color-text-primary)] hover:bg-[var(--color-bg-wash-hover)]"
            >
              <span className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={population[row.key]}
                  onChange={(e) => onChange({ ...population, [row.key]: e.target.checked })}
                  className="h-3.5 w-3.5 accent-[var(--color-accent-primary)]"
                />
                {row.label}
              </span>
              {typeof count === "number" && (
                <span className="tabular-nums text-[11px] text-[var(--color-text-tertiary)]">
                  {formatNumber(count)}
                </span>
              )}
            </label>
          );
        })}
      </PopoverContent>
    </Popover>
  );
}
