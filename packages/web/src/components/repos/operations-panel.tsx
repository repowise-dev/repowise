"use client";

import { useState } from "react";
import { toast } from "sonner";
import { RefreshCw, Zap, AlertTriangle, Download } from "lucide-react";
import { SettingsRow, SettingsRows } from "@repowise-dev/ui/settings";
import { syncRepo, fullResyncRepo } from "@/lib/api/repos";
import { Button } from "@repowise-dev/ui/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@repowise-dev/ui/ui/dialog";
import { GenerationProgressWrapper as GenerationProgress } from "@/components/jobs/generation-progress-wrapper";
import { useWatchedJob } from "@/lib/hooks/use-watched-job";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { useTranslations } from "next-intl";

interface Props {
  repoId: string;
  repoName: string;
}

export function OperationsPanel({ repoId, repoName }: Props) {
  const t = useTranslations("repos");
  const tc = useTranslations("common");
  const { watched, watch, settle, dismiss } = useWatchedJob<"sync" | "resync">();
  const [confirmResync, setConfirmResync] = useState(false);
  const [loading, setLoading] = useState<"sync" | "resync" | null>(null);

  async function handleSync() {
    setLoading("sync");
    try {
      const { job_id } = await syncRepo(repoId);
      watch(job_id, "sync");
      toast.info(t("ops.syncStarted", { name: repoName }));
    } catch (e) {
      toast.error(t("ops.syncFailed"), {
        description: toFriendlyMessage(e),
      });
    } finally {
      setLoading(null);
    }
  }

  async function handleResync() {
    setConfirmResync(false);
    setLoading("resync");
    try {
      const { job_id } = await fullResyncRepo(repoId);
      watch(job_id, "resync");
      toast.info(t("ops.resyncStarted", { name: repoName }));
    } catch (e) {
      toast.error(t("ops.resyncFailed"), {
        description: toFriendlyMessage(e),
      });
    } finally {
      setLoading(null);
    }
  }

  const busy = loading !== null || watched !== null;

  return (
    <>
      <SettingsRows>
        <SettingsRow label={t("ops.syncLabel")} hint={t("ops.syncHint")}>
          <div className="flex sm:justify-end">
            <Button size="sm" onClick={handleSync} disabled={busy}>
              <Zap className="mr-1.5 h-3.5 w-3.5" />
              {loading === "sync" ? t("ops.starting") : t("ops.sync")}
            </Button>
          </div>
        </SettingsRow>
        <SettingsRow label={t("ops.resyncLabel")} hint={t("ops.resyncHint")}>
          <div className="flex sm:justify-end">
            <Button variant="outline" size="sm" onClick={() => setConfirmResync(true)} disabled={busy}>
              <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
              {loading === "resync" ? t("ops.starting") : t("ops.resync")}
            </Button>
          </div>
        </SettingsRow>
      </SettingsRows>

      {watched && (
        <GenerationProgress
          key={watched.id}
          jobId={watched.id}
          repoName={repoName}
          onDone={settle}
          // A full re-index retry goes back through its confirmation.
          onRetry={watched.action === "resync" ? () => setConfirmResync(true) : handleSync}
          onDismiss={dismiss}
        />
      )}

      {/* Confirm resync dialog */}
      <Dialog open={confirmResync} onOpenChange={setConfirmResync}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-[var(--color-stale)]" />
              {t("ops.resync")}
            </DialogTitle>
          </DialogHeader>
          <p className="text-sm text-[var(--color-text-secondary)]">
            {t.rich("ops.resyncConfirm", {
              name: repoName,
              repo: (chunks) => (
                <span className="font-medium text-[var(--color-text-primary)]">
                  {chunks}
                </span>
              ),
            })}
          </p>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmResync(false)}>
              {tc("cancel")}
            </Button>
            <Button variant="destructive" onClick={handleResync}>
              {t("ops.resyncConfirmAction")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

/** Download this repository's index; rare, so it sits under Models' Advanced. */
export function ExportDocsRow({ repoId }: { repoId: string }) {
  const t = useTranslations("repos");
  return (
    <SettingsRow label={t("ops.exportLabel")} hint={t("ops.exportHint")}>
      <div className="flex sm:justify-end">
        <Button variant="outline" size="sm" asChild>
          <a href={`/api/repos/${encodeURIComponent(repoId)}/export`} download>
            <Download className="mr-1.5 h-3.5 w-3.5" />
            {t("ops.export")}
          </a>
        </Button>
      </div>
    </SettingsRow>
  );
}
