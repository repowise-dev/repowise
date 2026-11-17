"use client";

import * as React from "react";
import { Users, Search } from "lucide-react";
import type { OwnerListEntry } from "@repowise-dev/types/owners";
import { Input } from "../ui/input";
import { Skeleton } from "../ui/skeleton";
import { Switch } from "../ui/switch";
import { EmptyState } from "../shared/empty-state";
import { OverviewSection } from "../overview/section";
import { ownershipLabels, type OwnershipLabels } from "./labels";
import { OwnerTable, type OwnerSortKey } from "./owner-table";

export type { OwnerSortKey };

export interface OwnerDirectoryFilters {
  q: string;
  sort: OwnerSortKey;
  /** List bots and coding agents beside people. Off by default. */
  includeAutomation?: boolean;
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
  onShowAll?: () => void;
  /** @deprecated Use `onShowAll`; used only when `onShowAll` is absent. */
  onLoadMore?: () => void;
  onSelect: (owner: OwnerListEntry) => void;
  /** Bot and agent identities in the index; the toggle shows only when there are some. */
  automationCount?: number;
  /** Preferred over `onSelect`: gives every row a real URL. */
  hrefFor?: (owner: OwnerListEntry) => string;
  LinkComponent?: React.ElementType | undefined;
  /** Rendered in place of the table when the list could not load. */
  error?: React.ReactNode;
  labels?: Partial<OwnershipLabels> | undefined;
  /**
   * Accepted and ignored, for callers that still pass them: the distribution
   * bar and the section links moved to `OwnershipLede`.
   */
  distributionOwners?: OwnerListEntry[];
  base?: string;
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
  onLoadMore,
  onSelect,
  automationCount = 0,
  hrefFor,
  LinkComponent,
  error,
  labels: overrides,
}: OwnerDirectoryProps) {
  const labels = React.useMemo(() => ownershipLabels(overrides), [overrides]);
  const all = filters.includeAutomation ?? false;
  return (
    <OverviewSection
      title={labels.everyone}
      description={labels.everyoneDescription}
      action={
        <span className="font-mono text-caption uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
          {labels.people(total, all)}
        </span>
      }
    >
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <div className="relative w-full max-w-sm">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-[var(--color-text-tertiary)]" />
          <Input
            value={filters.q}
            onChange={(e) => onFiltersChange({ ...filters, q: e.target.value })}
            placeholder={labels.searchPlaceholder}
            aria-label={labels.searchLabel}
            className="pl-8"
          />
        </div>
        {automationCount > 0 && (
          <label className="flex items-center gap-2 text-xs text-[var(--color-text-secondary)]">
            <Switch
              checked={all}
              onCheckedChange={(includeAutomation) => onFiltersChange({ ...filters, includeAutomation })}
            />
            {labels.showAutomation(automationCount)}
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
            title={labels.noMatch}
            action={{ label: labels.clearSearch, onClick: () => onFiltersChange({ ...filters, q: "" }) }}
          />
        ) : (
          <EmptyState
            icon={<Users />}
            title={labels.noPeople}
            description={labels.noPeopleDescription}
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
          labels={labels}
        />
      )}

      {hasMore && (
        <button
          type="button"
          onClick={onShowAll ?? onLoadMore}
          disabled={isValidating}
          className="self-start text-xs text-[var(--color-accent-primary)] hover:underline disabled:text-[var(--color-text-tertiary)] disabled:no-underline"
        >
          {isValidating ? labels.loading : labels.showAllPeople(total, all)}
        </button>
      )}
    </OverviewSection>
  );
}
