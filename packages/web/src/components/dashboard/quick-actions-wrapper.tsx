"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  QuickActions as QuickActionsShell,
  type QuickActionKey,
} from "@repowise-dev/ui/dashboard/quick-actions";
import { syncRepo, fullResyncRepo } from "@/lib/api/repos";
import { listJobs } from "@/lib/api/jobs";
import { analyzeDeadCode } from "@/lib/api/dead-code";
import { GenerationProgressWrapper } from "@/components/jobs/generation-progress-wrapper";

interface Props {
  repoId: string;
  repoName?: string;
  pageCount?: number;
  modelName?: string;
  lastSyncAt?: string | null;
  lastResyncAt?: string | null;
}

export function QuickActionsWrapper({
  repoId,
  repoName,
  pageCount = 0,
  modelName = "",
  lastSyncAt,
  lastResyncAt,
}: Props) {
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  // Hydrate from any in-flight job so refreshes don't lose progress visibility.
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
        if (inflight) setActiveJobId(inflight.id);
      } catch {
        // best-effort
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [repoId]);

  async function handleAction(key: QuickActionKey) {
    try {
      if (key === "sync") {
        const job = await syncRepo(repoId);
        setActiveJobId(job.id);
        toast.info(`Sync started${repoName ? ` — ${repoName}` : ""}`);
      } else if (key === "resync") {
        const job = await fullResyncRepo(repoId);
        setActiveJobId(job.id);
        toast.info(`Full resync started${repoName ? ` — ${repoName}` : ""}`);
      } else if (key === "dead-code") {
        await analyzeDeadCode(repoId);
        toast.info("Dead code analysis started");
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Unknown error";
      if (/already in progress/i.test(msg)) {
        try {
          const [running, pending] = await Promise.all([
            listJobs({ repo_id: repoId, status: "running", limit: 1 }),
            listJobs({ repo_id: repoId, status: "pending", limit: 1 }),
          ]);
          const inflight = running[0] ?? pending[0];
          if (inflight) {
            setActiveJobId(inflight.id);
            toast.info(
              "Showing progress for the in-flight job. Cancel it from the panel to start a new one.",
            );
            return;
          }
        } catch {
          // fall through
        }
      }
      toast.error(`${key} failed`, { description: msg });
    }
  }

  const activeSlot = activeJobId ? (
    <GenerationProgressWrapper
      jobId={activeJobId}
      repoName={repoName}
      onDone={() => setActiveJobId(null)}
    />
  ) : null;

  return (
    <QuickActionsShell
      onAction={handleAction}
      lastSyncAt={lastSyncAt}
      lastResyncAt={lastResyncAt}
      pageCount={pageCount}
      modelName={modelName}
      activeJobSlot={activeSlot}
    />
  );
}
