"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import useSWRInfinite from "swr/infinite";
import {
  OwnerDirectory,
  type OwnerDirectoryFilters,
} from "@repowise-dev/ui/owners/owner-directory";
import { ApiError } from "@repowise-dev/ui/shared/api-error";
import { useDebounce } from "@/lib/hooks/use-debounce";
import { listOwnersPage } from "@/lib/api/owners";
import type { OwnerListEntry, Paginated } from "@/lib/api/types";
import { useOwnershipLabels } from "./ownership-labels";
import {
  directoryFiltersFromParams,
  directoryFiltersKey,
  externalDirectoryFilters,
  writeDirectoryFilters,
} from "./use-contributors-url";

/** Rows shown before "Show all", then the page size the rest loads in. */
const FIRST_PAGE = 10;
const REST_PAGE = 50;

const pageRange = (pageIndex: number) =>
  pageIndex === 0
    ? { offset: 0, limit: FIRST_PAGE }
    : { offset: FIRST_PAGE + (pageIndex - 1) * REST_PAGE, limit: REST_PAGE };

/** The Overview's "Everyone" table: people only unless the reader asks for automation. */
export function AllContributors({ repoId, automationCount }: { repoId: string; automationCount: number }) {
  const t = useTranslations("views.contributors");
  const tCommon = useTranslations("common");
  const router = useRouter();
  const searchParams = useSearchParams();
  const labels = useOwnershipLabels();
  const [filters, setFilters] = useState<OwnerDirectoryFilters>(() =>
    directoryFiltersFromParams(searchParams),
  );
  const debouncedQ = useDebounce(filters.q, 250);
  // The table's search, sort and toggle live in the URL, so a filtered view is a
  // link. The URL seeds the state; the table writes it on every debounced
  // change, and the first write only normalises a hand-edited query.
  // Writes not yet seen back, oldest first: a slow replace can land after the
  // next debounced write, and its echo must not revert the search box.
  const written = useRef<string[]>([]);
  useEffect(() => {
    const shown = { ...filters, q: debouncedQ };
    const next = writeDirectoryFilters(searchParams, shown);
    if (next === searchParams.toString()) return;
    written.current.push(directoryFiltersKey(shown));
    router.replace(next ? `?${next}` : "?", { scroll: false });
    // Only the debounced filters drive a write; reading searchParams must not loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQ, filters.sort, filters.includeAutomation]);
  // A URL the table did not write (a link followed while it stays mounted) wins.
  useEffect(() => {
    const adopt = externalDirectoryFilters(searchParams, written.current, { ...filters, q: debouncedQ });
    if (adopt) {
      written.current = [];
      setFilters(adopt);
      return;
    }
    const echo = written.current.indexOf(directoryFiltersKey(directoryFiltersFromParams(searchParams)));
    if (echo >= 0) written.current.splice(0, echo + 1);
    // Only a URL change is read here; the write above follows the filters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);
  const key = useMemo(
    () => JSON.stringify({ q: debouncedQ, sort: filters.sort, all: filters.includeAutomation }),
    [debouncedQ, filters.sort, filters.includeAutomation],
  );

  const { data, error, mutate, size, setSize, isLoading, isValidating } = useSWRInfinite<
    Paginated<OwnerListEntry>
  >(
    (pageIndex, previous) => {
      if (previous && !previous.has_more) return null;
      return `owners:${repoId}:${key}:${pageIndex}`;
    },
    (k) => {
      const pageIndex = parseInt(k.split(":").pop()!, 10);
      return listOwnersPage({
        repoId,
        q: debouncedQ || undefined,
        sort: filters.sort,
        ...(filters.includeAutomation ? {} : { kind: "human" as const }),
        ...pageRange(pageIndex),
      });
    },
    { revalidateOnFocus: false, revalidateFirstPage: false },
  );

  const items = useMemo(() => (data ? data.flatMap((p) => p.items) : []), [data]);
  const total = data?.[0]?.total ?? 0;
  const hrefFor = (o: OwnerListEntry) => `/repos/${repoId}/owners/${encodeURIComponent(o.key)}`;

  return (
    <OwnerDirectory
      owners={items}
      isLoading={isLoading}
      isValidating={isValidating}
      total={total}
      hasMore={data ? data[data.length - 1]!.has_more : false}
      filters={filters}
      onFiltersChange={setFilters}
      onShowAll={() => void setSize(Math.max(size, 1 + Math.ceil(Math.max(0, total - FIRST_PAGE) / REST_PAGE)))}
      automationCount={automationCount}
      hrefFor={hrefFor}
      LinkComponent={Link}
      labels={labels}
      error={
        error ? (
          <ApiError
            title={t("loadFailedTitle")}
            message={t("loadFailed")}
            retryLabel={tCommon("retry")}
            onRetry={() => void mutate()}
          />
        ) : undefined
      }
      onSelect={(o) => router.push(hrefFor(o))}
    />
  );
}
