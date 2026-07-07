"use client";

import { useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import useSWR from "swr";
import useSWRInfinite from "swr/infinite";
import { Users } from "lucide-react";
import {
  OwnerDirectory,
  type OwnerDirectoryFilters,
} from "@repowise-dev/ui/owners/owner-directory";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { useDebounce } from "@/lib/hooks/use-debounce";
import { listAllOwners, listOwnersPage } from "@/lib/api/owners";
import type { OwnerListEntry, Paginated } from "@/lib/api/types";

const LIMIT = 30;
/** When contributor count is at or below this, prefetch everyone for an
 *  accurate ownership distribution bar via fetchAllPaginated. */
const DISTRIBUTION_PREFETCH_CAP = 120;

export default function OwnersDirectoryPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [filters, setFilters] = useState<OwnerDirectoryFilters>({
    q: "",
    sort: "files_owned",
  });
  const debouncedQ = useDebounce(filters.q, 250);

  const key = useMemo(
    () => JSON.stringify({ q: debouncedQ, sort: filters.sort }),
    [debouncedQ, filters.sort],
  );

  const { data, size, setSize, isLoading, isValidating } = useSWRInfinite<
    Paginated<OwnerListEntry>
  >(
    (pageIndex, previous) => {
      if (previous && !previous.has_more) return null;
      return `owners:${id}:${key}:${pageIndex}`;
    },
    (k) => {
      const pageIndex = parseInt(k.split(":").pop()!, 10);
      return listOwnersPage({
        repoId: id,
        q: debouncedQ || undefined,
        sort: filters.sort,
        limit: LIMIT,
        offset: pageIndex * LIMIT,
      });
    },
    { revalidateOnFocus: false, revalidateFirstPage: false },
  );

  const items = useMemo(() => (data ? data.flatMap((p) => p.items) : []), [data]);
  const total = data && data.length > 0 ? data[0].total : 0;
  const hasMore = data ? data[data.length - 1].has_more : false;

  const shouldPrefetchDistribution =
    debouncedQ === "" && total > 0 && total <= DISTRIBUTION_PREFETCH_CAP;
  const { data: distributionOwners } = useSWR(
    shouldPrefetchDistribution ? `owners-all:${id}:${filters.sort}` : null,
    () =>
      listAllOwners({
        repoId: id,
        sort: filters.sort,
        maxItems: DISTRIBUTION_PREFETCH_CAP,
        pageSize: DISTRIBUTION_PREFETCH_CAP,
      }),
    { revalidateOnFocus: false },
  );

  return (
    <PageShell
      maxWidth="wide"
      icon={<Users className="h-5 w-5 text-[var(--color-accent-primary)]" />}
      title="Contributors"
      description="Who is doing what — ownership, hotspots, dead-code burden, and bus-factor risk per person. Click any card to open the full profile."
    >
      <OwnerDirectory
        owners={items}
        distributionOwners={distributionOwners}
        isLoading={isLoading}
        isValidating={isValidating}
        total={total}
        hasMore={hasMore}
        filters={filters}
        onFiltersChange={setFilters}
        onLoadMore={() => setSize(size + 1)}
        onSelect={(o) =>
          router.push(`/repos/${id}/owners/${encodeURIComponent(o.key)}`)
        }
      />
    </PageShell>
  );
}
