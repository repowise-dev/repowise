"use client";

/**
 * The map's search: one combobox in the title row, results grouped as
 * Services, Groups and Routes. Arrow keys move through every result, Enter
 * picks, Escape clears then leaves. The input is reachable from anywhere on
 * the page with `/` or Ctrl/Cmd+K through the ref the map holds.
 */

import { useId, useState, type RefObject } from "react";
import { Search } from "lucide-react";
import type { SystemMapNavCopy } from "./nav-copy";
import { SEARCH_MIN_CHARS, type SearchHit } from "./search-index";
import type { MapSearch } from "./use-system-map-search";
import { FIELD_CLASS } from "../../ui/input";
import { cn } from "../../lib/cn";

export interface SystemMapSearchProps {
  search: MapSearch;
  inputRef: RefObject<HTMLInputElement | null>;
  copy: SystemMapNavCopy;
  /** Display name of a service id, for route hits. */
  name: (serviceId: string) => string;
}

const SECTIONS: { kind: SearchHit["kind"]; title: (c: SystemMapNavCopy) => string }[] = [
  { kind: "service", title: (c) => c.searchServices },
  { kind: "group", title: (c) => c.searchGroups },
  { kind: "route", title: (c) => c.searchRoutes },
];

function hitDetail(hit: SearchHit, copy: SystemMapNavCopy, name: (id: string) => string): string {
  if (hit.kind !== "route") return hit.detail;
  return hit.consumer ? copy.routeCallers(name(hit.consumer), hit.otherConsumers) : copy.routeProvider(name(hit.provider));
}

export function SystemMapSearch({ search, inputRef, copy, name }: SystemMapSearchProps) {
  const listId = useId();
  const [open, setOpen] = useState(false);
  const shown = open && search.query.trim().length >= SEARCH_MIN_CHARS;
  const optionId = (i: number) => `${listId}-o${i}`;
  const activeHit = search.hits[search.active];

  return (
    <div className="relative w-full min-w-0 sm:w-64" onBlur={(e) => !e.currentTarget.contains(e.relatedTarget) && setOpen(false)}>
      <Search aria-hidden className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--color-text-tertiary)]" />
      <input
        ref={inputRef}
        type="search"
        role="combobox"
        aria-label={copy.searchLabel}
        aria-expanded={shown}
        aria-controls={shown ? listId : undefined}
        aria-autocomplete="list"
        aria-activedescendant={shown && activeHit ? optionId(search.active) : undefined}
        placeholder={copy.searchPlaceholder}
        value={search.query}
        onChange={(e) => {
          search.setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={search.onKeyDown}
        className={cn(FIELD_CLASS, "h-8 w-full pl-7 pr-2 text-xs")}
      />
      {shown && <SearchResults id={listId} optionId={optionId} search={search} copy={copy} name={name} />}
    </div>
  );
}

interface SearchResultsProps {
  id: string;
  optionId: (index: number) => string;
  search: MapSearch;
  copy: SystemMapNavCopy;
  name: (serviceId: string) => string;
}

/** The listbox: hits grouped by section, each an option the arrows highlight. */
function SearchResults({ id, optionId, search, copy, name }: SearchResultsProps) {
  const status = search.hits.length === 0 ? (search.routesLoading ? copy.searchRoutesLoading : copy.searchNone) : null;
  return (
    <div
      id={id}
      role="listbox"
      aria-label={copy.searchLabel}
      className="absolute right-0 top-full z-[var(--z-dropdown)] mt-1 max-h-80 w-full min-w-[16rem] overflow-y-auto rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-1 shadow-lg shadow-black/20"
    >
      {status && <p className="px-2 py-1.5 text-xs text-[var(--color-text-secondary)]">{status}</p>}
      {SECTIONS.map(({ kind, title }) => {
        const rows = search.hits.map((hit, i) => ({ hit, i })).filter(({ hit }) => hit.kind === kind);
        if (rows.length === 0) return null;
        return (
          <div key={kind} role="group" aria-label={title(copy)}>
            <p aria-hidden className="px-2 pb-0.5 pt-1.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
              {title(copy)}
            </p>
            {rows.map(({ hit, i }) => (
              <div
                key={`${hit.kind}:${hit.id}`}
                id={optionId(i)}
                role="option"
                aria-selected={i === search.active}
                // Keeps focus in the input, so the pick does not blur and close the list first.
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => search.pick(hit)}
                className={`flex cursor-pointer flex-col rounded px-2 py-1 ${i === search.active ? "bg-[var(--color-bg-inset)]" : "hover:bg-[var(--color-bg-inset)]"}`}
              >
                <span className={`truncate text-xs text-[var(--color-text-primary)] ${hit.kind === "route" ? "font-mono" : ""}`}>{hit.label}</span>
                <span className="truncate text-[10px] text-[var(--color-text-secondary)]">{hitDetail(hit, copy, name)}</span>
              </div>
            ))}
          </div>
        );
      })}
      {!status && search.routesLoading && (
        <p className="px-2 py-1 text-[10px] text-[var(--color-text-secondary)]">{copy.searchRoutesLoading}</p>
      )}
    </div>
  );
}
