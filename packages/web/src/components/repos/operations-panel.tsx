"use client";

import { useState } from "react";
import { toast } from "sonner";
import { RefreshCw, Zap, AlertTriangle, Download } from "lucide-react";
import { syncRepo, fullResyncRepo } from "@/lib/api/repos";
import { Button } from "@repowise-dev/ui/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise-dev/ui/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@repowise-dev/ui/ui/dialog";
import { GenerationProgressWrapper as GenerationProgress } from "@/components/jobs/generation-progress-wrapper";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { useTranslations } from "next-intl";

interface Props {
  repoId: string;
  repoName: string;
}

export function OperationsPanel({ repoId, repoName }: Props) {
  const t = useTranslations("repos");
  const tc = useTranslations("common");
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [confirmResync, setConfirmResync] = useState(false);
  const [loading, setLoading] = useState<"sync" | "resync" | null>(null);

  async function handleSync() {
    setLoading("sync");
    try {
      const job = await syncRepo(repoId);
      setActiveJobId(job.id);
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
      const job = await fullResyncRepo(repoId);
      setActiveJobId(job.id);
      toast.info(t("ops.resyncStarted", { name: repoName }));
    } catch (e) {
      toast.error(t("ops.resyncFailed"), {
        description: toFriendlyMessage(e),
      });
    } finally {
      setLoading(null);
    }
  }

  function handleJobDone() {
    setActiveJobId(null);
  }

  return (
    <>
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">{t("ops.title")}</CardTitle>
        </CardHeader>

        <CardContent id="operations-panel-content" className="space-y-4">
          {/* Active job progress */}
          {activeJobId && (
            <GenerationProgress
              jobId={activeJobId}
              repoName={repoName}
              onDone={handleJobDone}
            />
          )}

          {/* Action buttons */}
          {!activeJobId && (
            <div className="flex gap-2">
              <Button
                variant="default"
                size="sm"
                onClick={handleSync}
                disabled={loading !== null}
                className="flex-1"
              >
                <Zap className="h-3.5 w-3.5 mr-1.5" />
                {loading === "sync" ? t("ops.starting") : t("ops.sync")}
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setConfirmResync(true)}
                disabled={loading !== null}
                className="flex-1"
              >
                <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
                {loading === "resync" ? t("ops.starting") : t("ops.resync")}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                asChild
              >
                <a href={`/api/repos/${repoId}/export`} download>
                  <Download className="h-3.5 w-3.5 mr-1.5" />
                  {t("ops.export")}
                </a>
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

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
