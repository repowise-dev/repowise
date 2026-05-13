"use client";

import { useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import useSWRInfinite from "swr/infinite";
import { Users } from "lucide-react";
import {
  OwnerDirectory,
  type OwnerDirectoryFilters,
} from "@repowise-dev/ui/owners/owner-directory";
import { useDebounce } from "@/lib/hooks/use-debounce";
import { listOwnersPage } from "@/lib/api/owners";
import type { OwnerListEntry, Paginated } from "@/lib/api/types";

const LIMIT = 30;

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

  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-[1600px]">
      <div>
        <h1 className="text-xl font-semibold text-[var(--color-text-primary)] mb-1 flex items-center gap-2">
          <Users className="h-5 w-5 text-[var(--color-accent-primary)]" />
          Contributors
        </h1>
        <p className="text-sm text-[var(--color-text-secondary)]">
          Who is doing what — ownership, hotspots, dead-code burden, and bus-factor risk
          per person. Click any card to open the full profile.
        </p>
      </div>

      <OwnerDirectory
        owners={items}
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
    </div>
  );
}
