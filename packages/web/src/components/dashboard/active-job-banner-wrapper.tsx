"use client";

import useSWR from "swr";
import { ActiveJobBanner as ActiveJobBannerShell } from "@repowise-dev/ui/dashboard/active-job-banner";
import { listJobs } from "@/lib/api/jobs";
import type { Job } from "@repowise-dev/types/jobs";

interface Props {
  repoId: string;
}

export function ActiveJobBannerWrapper({ repoId }: Props) {
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

  const job = jobs?.[0] ?? null;
  if (!job) return null;

  // Drop completed/failed banners after a 60s freshness window — matches the
  // pre-extraction inline behaviour.
  if (job.status !== "running" && job.status !== "completed" && job.status !== "failed") {
    return null;
  }
  if (job.status !== "running") {
    const finishedAt = job.finished_at ? new Date(job.finished_at).getTime() : 0;
    if (Date.now() - finishedAt > 60_000) return null;
  }

  return <ActiveJobBannerShell job={job} detailsHref={`/repos/${repoId}/overview`} />;
}
