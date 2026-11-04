"use client";

import { useEffect, useRef, useState } from "react";
import { Search } from "lucide-react";
import type { HotspotFacets, HotspotFilter } from "@repowise-dev/types/git";
import { Input } from "../ui/input";
import { cn } from "../lib/cn";
import { useDebounce } from "../hooks/use-debounce";
import { PromptForRowsButton } from "../health/row-prompt-action";
import { formatNumber } from "../lib/format";

/** Wait after the last keystroke before the search goes to the server. */
const SEARCH_DEBOUNCE_MS = 250;

const CHIPS: { key: HotspotFilter; label: string; title?: string }[] = [
  { key: "all", label: "All" },
  {
    key: "bus_factor_1",
    label: "Bus factor 1",
    title: "One person holds the knowledge. Files with no attributable history are not counted.",
  },
  {
    key: "accelerating",
    label: "Accelerating",
    title: "More commits in the last 30 days than its 90-day pace, on files older than 90 days.",
  },
  { key: "bug_magnet", label: "Bug magnet", title: "Repeated recent bug fixes." },
];

/** The chips a facet set offers: a filter with nothing in it is not offered,
 * unless it is the active one (the reader needs it to see what is applied). */
export function hotspotChips(facets: HotspotFacets, active: HotspotFilter = "all") {
  return CHIPS.filter((c) => c.key === "all" || c.key === active || facets[c.key] > 0).map((c) => ({
    ...c,
    count: c.key === "all" ? facets.total : facets[c.key],
  }));
}

/** Search box that reports a settled query, and follows the URL when it changes. */
function SearchBox({ query, onChange }: { query: string; onChange: (q: string) => void }) {
  const [draft, setDraft] = useState(query);
  const settled = useDebounce(draft, SEARCH_DEBOUNCE_MS);
  // The last value sent up: its echo back through the URL must not overwrite
  // what the reader has typed since. Only an outside change resets the box.
  const sent = useRef(query);
  useEffect(() => {
    if (query !== sent.current) {
      sent.current = query;
      setDraft(query);
    }
  }, [query]);
  useEffect(() => {
    if (settled === sent.current) return;
    sent.current = settled;
    onChange(settled);
    // Keyed on the settled value alone; `onChange` is the host's latest setter.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settled]);
  return (
    <div className="relative">
      <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
      <Input
        type="search"
        aria-label="Search hotspots by file or owner"
        placeholder="Search files or owners…"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        className="pl-8 h-8 w-full sm:w-56 text-xs"
      />
    </div>
  );
}

export function HotspotToolbar({
  query,
  onQueryChange,
  filter,
  onFilterChange,
  facets,
  total,
  onPromptForRows,
  batchPromptLabel,
}: {
  query: string;
  onQueryChange?: ((q: string) => void) | undefined;
  filter: HotspotFilter;
  onFilterChange?: ((f: HotspotFilter) => void) | undefined;
  facets?: HotspotFacets | undefined;
  /** Rows the current filter and search match. */
  total: number;
  onPromptForRows?: (() => void) | undefined;
  batchPromptLabel?: ((count: number) => string) | undefined;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {onQueryChange && <SearchBox query={query} onChange={onQueryChange} />}
      {facets && onFilterChange && (
        <div
          role="group"
          aria-label="Filter hotspots"
          className="flex flex-wrap rounded-md border border-[var(--color-border-default)] overflow-hidden text-xs"
        >
          {hotspotChips(facets, filter).map((c) => (
            <button
              key={c.key}
              type="button"
              aria-pressed={filter === c.key}
              title={c.title}
              onClick={() => onFilterChange(c.key)}
              className={cn(
                "px-2.5 py-1.5 font-medium transition-colors",
                filter === c.key
                  ? "bg-[var(--color-control-selected)] text-[var(--color-control-selected-text)]"
                  : "bg-transparent text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]",
              )}
            >
              {c.label}
              <span className="ml-1.5 tabular-nums text-[10px] opacity-70">
                {c.key === "all"
                  ? formatNumber(c.count)
                  : `${formatNumber(c.count)} of ${formatNumber(facets.total)}`}
              </span>
            </button>
          ))}
        </div>
      )}
      {onPromptForRows && (
        <PromptForRowsButton
          count={total}
          onClick={onPromptForRows}
          {...(batchPromptLabel ? { label: batchPromptLabel } : {})}
          className="sm:ml-auto"
        />
      )}
    </div>
  );
}
