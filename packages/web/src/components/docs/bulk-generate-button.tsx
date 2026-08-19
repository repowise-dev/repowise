"use client";

import { Sparkles } from "lucide-react";
import { Button } from "@repowise-dev/ui/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@repowise-dev/ui/ui/dialog";
import { GenerationProgressWrapper } from "@/components/jobs/generation-progress-wrapper";
import { BulkGenerateConfirm } from "./bulk-generate-confirm";
import { useBulkGenerate } from "@/lib/hooks/use-bulk-generate";

/**
 * The one bulk generation action, mounted in the docs header. Writes every
 * subsystem page still rendered from structure, with a single cost estimate.
 * Shown by the header only while stubs remain, so it retires itself once the
 * wiki is fully written.
 */
export function BulkGenerateButton({
  repoId,
  onGenerated,
}: {
  repoId: string;
  /** Called once a run completes so the host can refresh the page list. */
  onGenerated?: () => void;
}) {
  const bulk = useBulkGenerate(repoId);

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        onClick={bulk.open}
        disabled={bulk.jobId != null}
        className="h-7 gap-1.5 border-[var(--color-accent-primary)]/40 bg-[var(--color-accent-muted)] text-xs text-[var(--color-accent-primary)] hover:bg-[var(--color-accent-muted)] hover:text-[var(--color-accent-hover)]"
      >
        <Sparkles className="h-3.5 w-3.5" />
        <span className="hidden sm:inline">Write subsystem pages</span>
      </Button>

      <BulkGenerateConfirm flow={bulk} repoId={repoId} title="Write the subsystem pages" />

      <Dialog
        open={bulk.jobId != null}
        onOpenChange={(v) => {
          if (!v) bulk.clearJob();
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Writing the subsystem pages</DialogTitle>
          </DialogHeader>
          {bulk.jobId != null && (
            <GenerationProgressWrapper
              jobId={bulk.jobId}
              onDone={() => onGenerated?.()}
            />
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
