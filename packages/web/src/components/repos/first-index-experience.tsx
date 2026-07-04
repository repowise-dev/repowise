"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sparkles } from "lucide-react";
import { toast } from "sonner";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { IndexCelebration } from "@repowise-dev/ui/onboarding/index-celebration";
import { FirstFiveFiles, type FirstFiveFile } from "@repowise-dev/ui/onboarding/first-five-files";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import { startIndexJob } from "@/lib/api/repos";
import { listJobs, getJob } from "@/lib/api/jobs";
import { getOverviewSummary } from "@/lib/api/overview";
import { GenerationProgressWrapper } from "@/components/jobs/generation-progress-wrapper";

interface Props {
  repoId: string;
  repoName: string;
}

/**
 * The fresh-repo overview surface: one prominent "Index this repo" action,
 * live phase-labeled progress once a job is running, and a celebratory
 * handoff (with the first five files to read) when the first index lands.
 * Hydrates from any in-flight job so a page refresh mid-index rejoins it.
 */
export function FirstIndexExperience({ repoId, repoName }: Props) {
  const router = useRouter();
  const [jobId, setJobId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [completed, setCompleted] = useState<{ pages: number } | null>(null);
  const [startHere, setStartHere] = useState<FirstFiveFile[]>([]);

  // Rejoin an in-flight first index after refresh/navigation.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [running, pending] = await Promise.all([
          listJobs({ repo_id: repoId, status: "running", limit: 1 }),
          listJobs({ repo_id: repoId, status: "pending", limit: 1 }),
        ]);
        if (cancelled) return;
        const inflight = running[0] ?? pending[0];
        if (inflight) setJobId(inflight.id);
      } catch {
        // best-effort
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [repoId]);

  const startIndex = useCallback(async () => {
    setStarting(true);
    try {
      const { job_id } = await startIndexJob(repoId);
      setCompleted(null);
      setJobId(job_id);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Unknown error";
      if (/already in progress/i.test(msg)) {
        // Attach to whatever is running instead of failing the click.
        try {
          const [running, pending] = await Promise.all([
            listJobs({ repo_id: repoId, status: "running", limit: 1 }),
            listJobs({ repo_id: repoId, status: "pending", limit: 1 }),
          ]);
          const inflight = running[0] ?? pending[0];
          if (inflight) {
            setJobId(inflight.id);
            return;
          }
        } catch {
          // fall through
        }
      }
      toast.error("Couldn't start indexing", { description: msg });
    } finally {
      setStarting(false);
    }
  }, [repoId]);

  const handleJobDone = useCallback(async () => {
    if (!jobId) return;
    try {
      const job = await getJob(jobId);
      if (job.status === "completed") {
        setCompleted({ pages: job.completed_pages });
        // Best-effort "start here" handoff; the celebration stands alone if
        // the summary isn't ready yet.
        try {
          const summary = await getOverviewSummary(repoId);
          setStartHere(
            summary.onboarding_targets.map((t) => ({
              file_path: t.path,
              pagerank: t.pagerank,
              has_doc: t.doc_words > 0,
            })),
          );
        } catch {
          // keep the celebration without the list
        }
      }
    } catch {
      // job fetch failed; leave the progress panel's terminal state visible
    }
  }, [jobId, repoId]);

  if (completed) {
    return (
      <IndexCelebration
        repoName={repoName}
        pagesGenerated={completed.pages}
        onExplore={() => router.refresh()}
      >
        {startHere.length > 0 && (
          <FirstFiveFiles
            files={startHere}
            title="Start here"
            // Targets can be symbol pages ("calc.py::add"); link to the file.
            hrefFor={(f) => fileEntityPath(`/repos/${repoId}`, f.file_path.split("::")[0]!)}
          />
        )}
      </IndexCelebration>
    );
  }

  if (jobId) {
    return (
      <div className="rounded-lg border border-[var(--color-border-default)] p-4">
        <GenerationProgressWrapper
          jobId={jobId}
          repoName={repoName}
          onDone={handleJobDone}
          onRetry={startIndex}
          quiet
        />
      </div>
    );
  }

  return (
    <EmptyState
      icon={<Sparkles className="h-8 w-8" />}
      title="This repo hasn't been indexed yet"
      description="One click builds the code index, health signals, and documentation. You can watch it happen live."
      action={{
        label: starting ? "Starting…" : "Index this repo",
        onClick: () => {
          if (!starting) void startIndex();
        },
      }}
    />
  );
}
