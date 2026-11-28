"use client";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@repowise-dev/ui/ui/dialog";
import { GenerationProgressWrapper } from "@/components/jobs/generation-progress-wrapper";
import { type useBulkGenerate } from "@/lib/hooks/use-bulk-generate";
import { useTranslations } from "next-intl";

/** The progress dialog of a `useBulkGenerate` run: open while its job is
 *  watched, retry reopens the confirm. */
export function BulkGenerateProgress({
  flow,
  onGenerated,
}: {
  flow: ReturnType<typeof useBulkGenerate>;
  /** Called once a run completes so the host can refresh what it shows. */
  onGenerated?: () => void;
}) {
  const t = useTranslations("docs");
  return (
    <Dialog
      open={flow.jobId != null}
      onOpenChange={(v) => {
        if (!v) flow.clearJob();
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("bulk.writing")}</DialogTitle>
        </DialogHeader>
        {flow.jobId != null && (
          <GenerationProgressWrapper
            key={flow.jobId}
            jobId={flow.jobId}
            onDone={(outcome) => {
              flow.settle(outcome);
              onGenerated?.();
            }}
            onRetry={() => {
              flow.clearJob();
              flow.open();
            }}
            onDismiss={flow.clearJob}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}
