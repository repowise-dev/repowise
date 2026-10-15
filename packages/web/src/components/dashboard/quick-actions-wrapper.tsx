"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import {
  QuickActions as QuickActionsShell,
  DEFAULT_QUICK_ACTIONS,
  type CostPer1k,
  type QuickActionKey,
} from "@repowise-dev/ui/dashboard/quick-actions";
import { syncRepo, fullResyncRepo } from "@/lib/api/repos";
import { listJobs } from "@/lib/api/jobs";
import { analyzeDeadCode } from "@/lib/api/dead-code";
import { GenerationProgressWrapper } from "@/components/jobs/generation-progress-wrapper";
import { useWatchedJob } from "@/lib/hooks/use-watched-job";
import type { JobOutcome } from "@repowise-dev/ui/jobs";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { useTranslations } from "next-intl";

interface Props {
  repoId: string;
  repoName?: string;
  pageCount?: number;
  modelName?: string;
  costPer1k?: CostPer1k | null;
  lastSyncAt?: string | null;
  lastResyncAt?: string | null;
  /** Button arrangement — see `QuickActionsProps.variant`. Overview passes
   *  `header` so sync is the one primary action. */
  variant?: "row" | "header";
}

/** Human label for an action key, for toast copy. */
function labelFor(key: QuickActionKey): string {
  return DEFAULT_QUICK_ACTIONS.find((a) => a.key === key)?.label ?? key;
}

/** The endpoint each action starts its job through. */
const LAUNCH: Record<QuickActionKey, (repoId: string) => Promise<{ job_id: string }>> = {
  sync: syncRepo,
  resync: fullResyncRepo,
  "dead-code": analyzeDeadCode,
};

/** The repo's running or queued job, if any. */
async function findInflightJob(repoId: string): Promise<{ id: string } | undefined> {
  const [running, pending] = await Promise.all([
    listJobs({ repo_id: repoId, status: "running", limit: 1 }),
    listJobs({ repo_id: repoId, status: "pending", limit: 1 }),
  ]);
  return running[0] ?? pending[0];
}

export function QuickActionsWrapper({
  repoId,
  repoName,
  pageCount = 0,
  modelName = "",
  costPer1k,
  lastSyncAt,
  lastResyncAt,
  variant,
}: Props) {
  const t = useTranslations("dashboard");
  const router = useRouter();
  /** Action labels in the active locale, for toast copy. */
  const actionLabel = (key: QuickActionKey): string =>
    key === "sync"
      ? t("quickActions.sync")
      : key === "resync"
        ? t("quickActions.resync")
        : key === "dead-code"
          ? t("quickActions.deadCode")
          : labelFor(key);
  // The job on screen and which action started it, so its completion is
  // reported in its own terms and a retry re-runs the same action.
  const { watched, watch: startWatching, watchIfIdle, settle, dismiss } =
    useWatchedJob<QuickActionKey>();
  // Set by a retry; the shell opens that action as if clicked.
  const [confirmRequest, setConfirmRequest] = useState<QuickActionKey | null>(null);

  // Hydrate from any in-flight job so refreshes don't lose progress visibility.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const inflight = await findInflightJob(repoId);
        if (!cancelled && inflight) watchIfIdle(inflight.id);
      } catch {
        // best-effort
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [repoId, watchIfIdle]);

  function handleJobDone(outcome: JobOutcome) {
    const key = watched?.action;
    settle(outcome);
    if (key === "dead-code") {
      if (outcome === "completed") toast.success(t("quickActions.deadCodeFinished"));
      else if (outcome === "failed") toast.error(t("quickActions.deadCodeFailed"));
    }
    // This page is server-rendered: without a refresh the "Dead Exports" tile
    // the scan exists to move keeps showing its pre-scan number, and the same
    // goes for every stat a sync, re-index or bulk generate just changed.
    if (outcome === "completed" || outcome === "warning") router.refresh();
  }

  function startedMessage(key: QuickActionKey): string {
    const name = repoName ? ` — ${repoName}` : "";
    if (key === "sync") return t("quickActions.syncStarted", { name });
    if (key === "resync") return t("quickActions.resyncStarted", { name });
    return t("quickActions.deadCodeStarted");
  }

  // A retry goes back through the action's own confirm step, which the shell
  // renders once the panel is gone.
  function requestRetry(key: QuickActionKey) {
    dismiss();
    setConfirmRequest(key);
  }

  async function handleAction(key: QuickActionKey) {
    try {
      const { job_id } = await LAUNCH[key](repoId);
      startWatching(job_id, key);
      toast.info(startedMessage(key));
    } catch (e) {
      const msg = toFriendlyMessage(e);
      if (/already in progress/i.test(msg)) {
        try {
          const inflight = await findInflightJob(repoId);
          if (inflight) {
            // Someone else's job: watch it, but do not claim it as this action.
            startWatching(inflight.id, null);
            toast.info(t("quickActions.inFlight"));
            return;
          }
        } catch {
          // fall through
        }
      }
      toast.error(t("quickActions.actionFailed", { action: actionLabel(key) }), {
        description: msg,
      });
    }
  }

  const retryAction = watched?.action;
  const activeSlot = watched ? (
    <GenerationProgressWrapper
      // A fresh instance per job, so a retry reports its own outcome.
      key={watched.id}
      jobId={watched.id}
      repoName={repoName}
      // A dead-code scan runs the index-only pipeline, so the generic
      // "Documentation updated - 0 pages generated" toast would describe it
      // wrongly. It reports itself below instead.
      quiet={retryAction === "dead-code"}
      onDone={handleJobDone}
      onRetry={retryAction ? () => requestRetry(retryAction) : undefined}
      onDismiss={dismiss}
    />
  ) : null;

  return (
    <QuickActionsShell
      actions={DEFAULT_QUICK_ACTIONS}
      onAction={handleAction}
      lastSyncAt={lastSyncAt}
      lastResyncAt={lastResyncAt}
      pageCount={pageCount}
      modelName={modelName}
      costPer1k={costPer1k}
      activeJobSlot={activeSlot}
      confirmRequest={confirmRequest}
      onConfirmRequestHandled={() => setConfirmRequest(null)}
      variant={variant}
    />
  );
}
