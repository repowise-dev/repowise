"use client";

import * as React from "react";
import useSWR from "swr";
import {
  DecisionsTable,
  type DecisionsTableFilters,
} from "@repowise-dev/ui/decisions/decisions-table";
import { listDecisions } from "@/lib/api/decisions";
import type { DecisionRecord } from "@repowise-dev/types/decisions";

interface DecisionsTableWrapperProps {
  repoId: string;
  initialData?: DecisionRecord[];
}

export function DecisionsTableWrapper({
  repoId,
  initialData,
}: DecisionsTableWrapperProps) {
  const [filters, setFilters] = React.useState<DecisionsTableFilters>({
    status: "all",
    source: "all",
  });

  const { data, error, mutate, isLoading } = useSWR(
    [`/api/repos/${repoId}/decisions`, filters.status, filters.source],
    () =>
      listDecisions(repoId, {
        status: filters.status !== "all" ? filters.status : undefined,
        source: filters.source !== "all" ? filters.source : undefined,
        include_proposed: true,
        limit: 100,
      }),
    { fallbackData: initialData },
  );

  return (
    <DecisionsTable
      decisions={data}
      filters={filters}
      onFiltersChange={setFilters}
      repoId={repoId}
      error={error}
      isLoading={isLoading}
      onRetry={() => mutate()}
    />
  );
}
