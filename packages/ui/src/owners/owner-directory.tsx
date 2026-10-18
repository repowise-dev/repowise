"use client";

import * as React from "react";
import { Users, Search } from "lucide-react";
import type { OwnerListEntry } from "@repowise-dev/types/owners";
import { Input } from "../ui/input";
import { Skeleton } from "../ui/skeleton";
import { Switch } from "../ui/switch";
import { EmptyState } from "../shared/empty-state";
import { OverviewSection } from "../overview/section";
import { OwnerTable, type OwnerSortKey } from "./owner-table";

export type { OwnerSortKey };

export interface OwnerDirectoryFilters {
  q: string;
  sort: OwnerSortKey;
  /** List bots and coding agents beside people. Off by default. */
  includeAutomation: boolean;
}

export interface OwnerDirectoryProps {
  owners: OwnerListEntry[];
  isLoading: boolean;
  isValidating: boolean;
  total: number;
  hasMore: boolean;
  filters: OwnerDirectoryFilters;
  onFiltersChange: (next: OwnerDirectoryFilters) => void;
  /** Load every remaining row; offered while `hasMore`. */
  onShowAll: () => void;
  onSelect: (owner: OwnerListEntry) => void;
  /** Bot and agent identities in the index; the toggle shows only when there are some. */
  automationCount?: number;
  /** Preferred over `onSelect`: gives every row a real URL. */
  hrefFor?: (owner: OwnerListEntry) => string;
  LinkComponent?: React.ElementType | undefined;
  /** Rendered in place of the table when the list could not load. */
  error?: React.ReactNode;
}

/**
 * Everyone, as one aligned table under the page's lede: the first rows, then
 * "Show all N". People only by default; bots and coding agents are a toggle
 * away.
 */
export function OwnerDirectory({
  owners,
  isLoading,
  isValidating,
  total,
  hasMore,
  filters,
  onFiltersChange,
  onShowAll,
  onSelect,
  automationCount = 0,
  hrefFor,
  LinkComponent,
  error,
}: OwnerDirectoryProps) {
  const noun = filters.includeAutomation
    ? total === 1 ? "contributor" : "contributors"
    : total === 1 ? "person" : "people";
  return (
    <OverviewSection
      title="Everyone"
      description="A dot marks sole-owned files held by someone who has stopped committing, so a clean column means nothing to chase."
      action={
        <span className="font-mono text-caption uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
          {total.toLocaleString()} {noun}
        </span>
      }
    >
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <div className="relative w-full max-w-sm">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-[var(--color-text-tertiary)]" />
          <Input
            value={filters.q}
            onChange={(e) => onFiltersChange({ ...filters, q: e.target.value })}
            placeholder="Filter by name or email…"
            aria-label="Filter contributors by name or email"
            className="pl-8"
          />
        </div>
        {automationCount > 0 && (
          <label className="flex items-center gap-2 text-xs text-[var(--color-text-secondary)]">
            <Switch
              checked={filters.includeAutomation}
              onCheckedChange={(includeAutomation) => onFiltersChange({ ...filters, includeAutomation })}
            />
            Show {automationCount.toLocaleString()} bot and agent{" "}
            {automationCount === 1 ? "identity" : "identities"}
          </label>
        )}
      </div>

      {error ? (
        error
      ) : isLoading && owners.length === 0 ? (
        <div className="flex flex-col gap-px">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-[54px] w-full" />
          ))}
        </div>
      ) : owners.length === 0 ? (
        filters.q ? (
          <EmptyState
            tone="filtered"
            title="No contributors match this search"
            action={{ label: "Clear search", onClick: () => onFiltersChange({ ...filters, q: "" }) }}
          />
        ) : (
          <EmptyState
            icon={<Users />}
            title="No contributors yet"
            description="Contributors land with the first git index."
          />
        )
      ) : (
        <OwnerTable
          owners={owners}
          sort={filters.sort}
          onSortChange={(sort) => onFiltersChange({ ...filters, sort })}
          hrefFor={hrefFor}
          onSelect={hrefFor ? undefined : onSelect}
          LinkComponent={LinkComponent}
        />
      )}

      {hasMore && (
        <button
          type="button"
          onClick={onShowAll}
          disabled={isValidating}
          className="self-start text-xs text-[var(--color-accent-primary)] hover:underline disabled:text-[var(--color-text-tertiary)] disabled:no-underline"
        >
          {isValidating ? "Loading…" : `Show all ${total.toLocaleString()} ${noun}`}
        </button>
      )}
    </OverviewSection>
  );
}
