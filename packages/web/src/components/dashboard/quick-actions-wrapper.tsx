"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import {
  QuickActions as QuickActionsShell,
  DEFAULT_QUICK_ACTIONS,
  GENERATE_DOCS_ACTION,
  type QuickActionKey,
} from "@repowise-dev/ui/dashboard/quick-actions";
import { syncRepo, fullResyncRepo } from "@/lib/api/repos";
import { listJobs } from "@/lib/api/jobs";
import { analyzeDeadCode } from "@/lib/api/dead-code";
import { GenerationProgressWrapper } from "@/components/jobs/generation-progress-wrapper";
import { BulkGenerateConfirm } from "@/components/docs/bulk-generate-confirm";
import { useBulkGenerate } from "@/lib/hooks/use-bulk-generate";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import type { RepoResponse } from "@/lib/api/types";

interface Props {
  repoId: string;
  repoName?: string;
  pageCount?: number;
  modelName?: string;
  lastSyncAt?: string | null;
  lastResyncAt?: string | null;
  /** When "deterministic" the repo still has template pages, so the "Write with
   *  AI" bulk action is offered. */
  docsMode?: RepoResponse["docs_mode"];
}

/** Human label for an action key, for toast copy. */
function labelFor(key: QuickActionKey): string {
  return (
    [GENERATE_DOCS_ACTION, ...DEFAULT_QUICK_ACTIONS].find((a) => a.key === key)?.label ?? key
  );
}

export function QuickActionsWrapper({
  repoId,
  repoName,
  pageCount = 0,
  modelName = "",
  lastSyncAt,
  lastResyncAt,
  docsMode,
}: Props) {
  const router = useRouter();
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  // Which action started the job on screen, so its completion can be reported
  // and refreshed in its own terms rather than as a generic doc generation.
  const [activeKey, setActiveKey] = useState<QuickActionKey | null>(null);
  const bulk = useBulkGenerate(repoId);

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

  const actions = useMemo(
    () =>
      docsMode === "deterministic"
        ? [GENERATE_DOCS_ACTION, ...DEFAULT_QUICK_ACTIONS]
        : DEFAULT_QUICK_ACTIONS,
    [docsMode],
  );

  function startWatching(jobId: string, key: QuickActionKey | null) {
    setActiveJobId(jobId);
    setActiveKey(key);
  }

  function handleJobDone(status: "completed" | "failed" | "cancelled") {
    const key = activeKey;
    setActiveJobId(null);
    setActiveKey(null);
    bulk.clearJob();
    if (key === "dead-code") {
      if (status === "completed") toast.success("Dead code scan finished");
      else if (status === "failed") toast.error("Dead code scan failed");
    }
    // This page is server-rendered: without a refresh the "Dead Exports" tile
    // the scan exists to move keeps showing its pre-scan number, and the same
    // goes for every stat a sync, re-index or bulk generate just changed.
    if (status === "completed") router.refresh();
  }

  async function handleAction(key: QuickActionKey) {
    if (key === "generate-docs") {
      bulk.begin(
        { kind: "unwritten" },
        { label: "every page still generated from structure", defaultCascade: "none" },
      );
      return;
    }
    try {
      if (key === "sync") {
        const job = await syncRepo(repoId);
        startWatching(job.id, key);
        toast.info(`Sync started${repoName ? ` — ${repoName}` : ""}`);
      } else if (key === "resync") {
        const job = await fullResyncRepo(repoId);
        startWatching(job.id, key);
        toast.info(`Full resync started${repoName ? ` — ${repoName}` : ""}`);
      } else if (key === "dead-code") {
        const { job_id } = await analyzeDeadCode(repoId);
        startWatching(job_id, key);
        toast.info("Dead code analysis started");
      }
    } catch (e) {
      const msg = toFriendlyMessage(e);
      if (/already in progress/i.test(msg)) {
        try {
          const [running, pending] = await Promise.all([
            listJobs({ repo_id: repoId, status: "running", limit: 1 }),
            listJobs({ repo_id: repoId, status: "pending", limit: 1 }),
          ]);
          const inflight = running[0] ?? pending[0];
          if (inflight) {
            // Someone else's job: watch it, but do not claim it as this action.
            startWatching(inflight.id, null);
            toast.info(
              "Showing progress for the in-flight job. Cancel it from the panel to start a new one.",
            );
            return;
          }
        } catch {
          // fall through
        }
      }
      toast.error(`${labelFor(key)} failed`, { description: msg });
    }
  }

  // A bulk generate job shares the same progress slot as sync/resync.
  const jobId = activeJobId ?? bulk.jobId;
  const activeSlot = jobId ? (
    <GenerationProgressWrapper
      jobId={jobId}
      repoName={repoName}
      // A dead-code scan runs the index-only pipeline, so the generic
      // "Documentation updated - 0 pages generated" toast would describe it
      // wrongly. It reports itself below instead.
      quiet={activeKey === "dead-code"}
      onDone={handleJobDone}
    />
  ) : null;

  return (
    <>
      <QuickActionsShell
        actions={actions}
        onAction={handleAction}
        lastSyncAt={lastSyncAt}
        lastResyncAt={lastResyncAt}
        pageCount={pageCount}
        modelName={modelName}
        activeJobSlot={activeSlot}
      />
      <BulkGenerateConfirm flow={bulk} repoId={repoId} />
    </>
  );
}
