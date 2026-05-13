"use client";

import { useMemo, useState } from "react";
import useSWRInfinite from "swr/infinite";
import { useRouter } from "next/navigation";
import { Folder } from "lucide-react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@repowise-dev/ui/ui/select";
import { ModuleHealthList } from "@repowise-dev/ui/modules/module-health-list";
import { listModuleHealth, type ModuleHealthSortKey } from "@/lib/api/modules";
import type { ModuleHealthSummary, Paginated } from "@/lib/api/types";

const PAGE = 30;
const SORT_LABELS: Record<ModuleHealthSortKey, string> = {
  health_score: "Lowest health first",
  hotspot_count: "Most hotspots",
  dead_code_lines: "Most dead code",
  file_count: "Largest modules",
};

/**
 * "Modules" tab on /risk — engineering-leader rollup of churn / ownership /
 * docs / dead-code / decisions per top-level module.
 */
export function ModulesTab({ repoId }: { repoId: string }) {
  const router = useRouter();
  const [sort, setSort] = useState<ModuleHealthSortKey>("health_score");

  const { data, isLoading, isValidating, size, setSize } = useSWRInfinite<
    Paginated<ModuleHealthSummary>
  >(
    (pageIndex, previous) => {
      if (previous && !previous.has_more) return null;
      return `module-health:${repoId}:${sort}:${pageIndex}`;
    },
    (key) => {
      const pageIndex = parseInt(key.split(":").pop()!, 10);
      return listModuleHealth(repoId, {
        sort,
        limit: PAGE,
        offset: pageIndex * PAGE,
      });
    },
    { revalidateOnFocus: false, revalidateFirstPage: false },
  );

  const items = useMemo(() => (data ? data.flatMap((p) => p.items) : []), [data]);
  const total = data && data.length > 0 ? data[0].total : 0;
  const hasMore = data ? data[data.length - 1].has_more : false;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
          <Folder className="h-4 w-4" />
          <span>
            Health rollup across every top-level module — composite of churn, ownership,
            docs, dead-code and decisions.
          </span>
        </div>
        <Select value={sort} onValueChange={(v) => setSort(v as ModuleHealthSortKey)}>
          <SelectTrigger className="w-[200px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {Object.entries(SORT_LABELS).map(([k, label]) => (
              <SelectItem key={k} value={k}>
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <ModuleHealthList
        modules={items}
        isLoading={isLoading}
        total={total}
        hasMore={hasMore}
        loadingMore={isValidating && !isLoading}
        onLoadMore={() => setSize(size + 1)}
        onSelect={(m) =>
          router.push(`/repos/${repoId}/modules/${encodeURIComponent(m.module_path)}`)
        }
      />
    </div>
  );
}
