"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import { useTranslations } from "next-intl";
import {
  ActiveJobBanner as ActiveJobBannerShell,
  type ActiveJobBannerLabels,
} from "@repowise-dev/ui/dashboard/active-job-banner";
import { jobOutcome } from "@repowise-dev/ui/jobs";
import { listJobs } from "@/lib/api/jobs";
import { providerSetupHref } from "@/lib/utils/page-href";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import type { Job } from "@repowise-dev/types/jobs";

interface Props {
  repoId: string;
}

/** A clean finish is an acknowledgement; a problem stays until it is read. */
const DONE_WINDOW_MS = 60_000;
const PROBLEM_WINDOW_MS = 24 * 60 * 60_000;

export function ActiveJobBannerWrapper({ repoId }: Props) {
  const t = useTranslations("jobs.banner");
  const [dismissedId, setDismissedId] = useState<string | null>(null);
  const { data: jobs } = useSWR<Job[]>(
    `/api/jobs?repo_id=${repoId}&limit=1`,
    () => listJobs({ repo_id: repoId, limit: 1 }),
    {
      refreshInterval: (data) => {
        const j = data?.[0];
        if (j?.status === "running") return 5000;
        return 30000;
      },
    },
  );
  const labels = useMemo<Partial<ActiveJobBannerLabels>>(
    () => ({
      finishedWithProblems: (action) => t("finishedWithProblems", { action }),
      pagesFailed: (count) => t("pagesFailed", { count: formatNumber(count) }),
      providerSettings: t("providerSettings"),
      dismiss: t("dismiss"),
    }),
    [t],
  );

  const job = jobs?.[0] ?? null;
  if (!job || job.id === dismissedId) return null;

  const outcome = job.status === "running" ? "running" : jobOutcome(job);
  if (outcome === null || outcome === "cancelled") return null;
  if (outcome !== "running") {
    const finishedAt = job.finished_at ? new Date(job.finished_at).getTime() : 0;
    const visibleFor = outcome === "completed" ? DONE_WINDOW_MS : PROBLEM_WINDOW_MS;
    if (Date.now() - finishedAt > visibleFor) return null;
  }

  return (
    <ActiveJobBannerShell
      job={job}
      detailsHref={`/repos/${repoId}/overview`}
      settingsHref={providerSetupHref(repoId)}
      onDismiss={() => setDismissedId(job.id)}
      labels={labels}
    />
  );
}
