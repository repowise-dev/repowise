"use client";

/**
 * Fix first on the Code Health page: binds the shared {@link FixFirstList} to
 * web's `/api` client and `/repos/:id` routes. The queue arrives as core built
 * it; nothing here reorders, groups or recounts it.
 */

import { useCallback, useState } from "react";
import Link from "next/link";
import useSWR, { useSWRConfig } from "swr";
import { FixFirstList } from "@repowise-dev/ui/health";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import type { FixFirstQueue, FixItem, FixScope } from "@repowise-dev/types/fix-first";
import { getFixFirst, updateFindingStatus } from "@/lib/api/code-health";

/** Items the section lists. The server caps the route at 50. */
const LIMIT = 10;

export function fixPlanHref(repoId: string, item: FixItem): string | null {
  const id = item.source.opportunity_id;
  if (!id) return null;
  const opp = encodeURIComponent(id);
  if (item.kind === "refactor") return `/repos/${repoId}/refactoring?opportunity=${opp}`;
  if (item.kind === "perf_fix") return `/repos/${repoId}/code-health?tab=performance&opportunity=${opp}`;
  return null;
}

export function FixFirstSection({ repoId, repoName }: { repoId: string; repoName?: string }) {
  const [scope, setScope] = useState<FixScope>("production");
  const key = `code-health-fix-first:${repoId}:${scope}`;
  const { data, error, isLoading, mutate } = useSWR<FixFirstQueue>(
    key,
    () => getFixFirst(repoId, { limit: LIMIT, scope }),
    { revalidateOnFocus: false, keepPreviousData: true },
  );
  const { mutate: mutateAll } = useSWRConfig();

  const onTriage = useCallback(
    async (item: FixItem, status: "acknowledged" | "resolved" | "false_positive") => {
      // A handful of ids per item; each is the existing per-finding PATCH.
      await Promise.all(
        item.source.finding_ids.map((id) => updateFindingStatus(repoId, id, status)),
      );
      // The queue and every health view that counts findings re-read.
      await mutateAll(
        (k) => typeof k === "string" && k.startsWith("code-health") && k.includes(repoId),
        undefined,
        { revalidate: true },
      );
    },
    [repoId, mutateAll],
  );

  const prefix = `/repos/${repoId}`;
  return (
    <FixFirstList
      queue={data}
      loading={isLoading}
      error={error}
      onRetry={() => void mutate()}
      scope={scope}
      onScopeChange={setScope}
      fileHref={(path) => fileEntityPath(prefix, path)}
      planHref={(item) => fixPlanHref(repoId, item)}
      onTriage={onTriage}
      LinkComponent={Link}
      {...(repoName ? { repoName } : {})}
    />
  );
}
