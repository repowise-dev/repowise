"use client";

import Link from "next/link";
import type { StatsHighlights } from "@repowise-dev/types/stats";
import { StatsReport } from "@repowise-dev/ui/stats/stats-report";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import { useWeekendDays } from "@/lib/hooks/use-weekend";

/** Client boundary for the Stats page: the weekend preference lives in
 *  localStorage, and the route builders are functions a server component
 *  cannot pass down. */
export function StatsView({ data, repoId }: { data: StatsHighlights; repoId: string }) {
  const weekendDays = useWeekendDays();
  const prefix = `/repos/${repoId}`;
  return (
    <StatsReport
      data={data}
      weekendDays={weekendDays}
      contributorsHref={`${prefix}/owners`}
      fileHref={(path) => fileEntityPath(prefix, path)}
      commitHref={(sha) => `${prefix}/commits?commit=${encodeURIComponent(sha)}`}
      LinkComponent={Link}
    />
  );
}
