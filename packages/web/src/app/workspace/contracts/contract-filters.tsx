"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, useTransition } from "react";
import { Search } from "lucide-react";
import { PaginationControls } from "@repowise-dev/ui/shared/pagination-controls";
import { ViewTabs } from "@repowise-dev/ui/shared/view-tabs";
import { contractTypeLabel } from "@repowise-dev/ui/workspace/contract-type-badge";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import { contractsListHref, type ContractListFilters } from "./contract-href";

/** Every type the extractors can emit, in tab order. */
const ALL_TYPES = ["http", "grpc", "socket", "topic", "data", "code"];

/** Keystrokes settle for this long before the list is re-fetched. */
const SEARCH_DEBOUNCE_MS = 300;

/**
 * The type tabs, from the workspace-wide distribution rather than the
 * vocabulary: a tab that can never return a row is a control that cannot act.
 * `byType` must be the unfiltered breakdown, or picking a type would delete
 * every other tab. A type someone linked to stays, so the URL never selects a
 * tab that is not drawn.
 */
function typeTabs(byType: Record<string, number> | null, selected: string) {
  const known = byType ? ALL_TYPES.filter((t) => (byType[t] ?? 0) > 0) : ALL_TYPES;
  const extra = byType
    ? Object.keys(byType).filter((t) => !ALL_TYPES.includes(t) && byType[t]! > 0)
    : [];
  const values = [...known, ...extra];
  if (selected && !values.includes(selected)) values.push(selected);
  const total = byType ? Object.values(byType).reduce((a, b) => a + b, 0) : null;
  return [
    { id: "", label: "All", ...(total != null ? { badge: formatNumber(total) } : {}) },
    ...values.map((t) => ({
      id: t,
      label: contractTypeLabel(t),
      ...(byType?.[t] ? { badge: formatNumber(byType[t]!) } : {}),
    })),
  ];
}

const SELECT_CLASS =
  "rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2.5 py-1.5 text-xs text-[var(--color-text-primary)] outline-none focus-visible:border-[var(--color-border-active)] focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] disabled:opacity-60";

/**
 * The list's controls. State lives in the URL, so a filtered view is linkable
 * and the server renders the rows and their count from the same request. Each
 * axis has one control: type is the tab row, the rest narrow within it.
 */
export function ContractListControls({
  filters,
  repos,
  byType,
}: {
  filters: ContractListFilters;
  repos: string[];
  /** Workspace-wide contracts per type, from `contract_summary`. */
  byType: Record<string, number> | null;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [text, setText] = useState(filters.q ?? "");
  const current = useRef(filters);
  current.current = filters;

  // Follow the URL when something else changes it (Back, a Browse link). The
  // box's own debounced write is recognised and skipped, so text typed while
  // that write was in flight is never overwritten.
  const urlQ = filters.q ?? "";
  const ownWrite = useRef<string | null>(null);
  useEffect(() => {
    if (ownWrite.current === urlQ) {
      ownWrite.current = null;
      return;
    }
    setText(urlQ);
  }, [urlQ]);

  const go = (next: ContractListFilters) => {
    // Any change of filter starts the list from its first page.
    const href = contractsListHref({ ...next, page: undefined }).split("#")[0]!;
    startTransition(() => router.replace(href, { scroll: false }));
  };

  useEffect(() => {
    const q = text.trim();
    if (q === (current.current.q ?? "")) return;
    const id = window.setTimeout(() => {
      ownWrite.current = q;
      go({ ...current.current, q });
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(id);
    // `go` reads the latest filters through the ref; only the text schedules.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);

  return (
    <div className="flex flex-col gap-3">
      <ViewTabs
        aria-label="Contract type"
        tabs={typeTabs(byType, filters.type ?? "")}
        value={filters.type ?? ""}
        onValueChange={(type) => go({ ...filters, type })}
      />
      <div className="flex flex-wrap items-center gap-2" aria-busy={pending}>
        <label className="relative min-w-[220px] flex-1">
          <span className="sr-only">Search contracts</span>
          <Search
            className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--color-text-tertiary)]"
            aria-hidden
          />
          <input
            type="search"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Search path, table, file, symbol or service"
            className={`${SELECT_CLASS} w-full pl-8`}
          />
        </label>

        <label className="sr-only" htmlFor="contract-repo">
          Repository
        </label>
        <select
          id="contract-repo"
          className={SELECT_CLASS}
          value={filters.repo ?? ""}
          onChange={(e) => go({ ...filters, repo: e.target.value })}
        >
          <option value="">All repositories</option>
          {repos.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>

        <label className="sr-only" htmlFor="contract-role">
          Role
        </label>
        <select
          id="contract-role"
          className={SELECT_CLASS}
          value={filters.role ?? ""}
          onChange={(e) => go({ ...filters, role: e.target.value })}
        >
          <option value="">Providers and consumers</option>
          <option value="provider">Providers</option>
          <option value="consumer">Consumers</option>
        </select>

        <label className="sr-only" htmlFor="contract-linked">
          Link
        </label>
        <select
          id="contract-linked"
          className={SELECT_CLASS}
          value={filters.linked ?? ""}
          onChange={(e) => go({ ...filters, linked: e.target.value })}
        >
          <option value="">Linked or not</option>
          <option value="yes">On a matched link</option>
          <option value="no">On no link</option>
        </select>

        {pending ? (
          <span className="text-[10px] text-[var(--color-text-tertiary)]" role="status">
            Updating...
          </span>
        ) : null}
      </div>
    </div>
  );
}

/** Previous and next page of the list, keeping every filter. */
export function ContractListPager({
  filters,
  page,
  pageSize,
  shown,
  total,
}: {
  filters: ContractListFilters;
  page: number;
  pageSize: number;
  shown: number;
  total: number;
}) {
  const router = useRouter();
  const to = (p: number) =>
    router.push(contractsListHref({ ...filters, page: p }), { scroll: false });
  const offset = (page - 1) * pageSize;
  return (
    <PaginationControls
      offset={offset}
      shown={shown}
      total={total}
      label="contracts"
      onPrevious={page > 1 ? () => to(page - 1) : undefined}
      onNext={offset + shown < total ? () => to(page + 1) : undefined}
    />
  );
}
