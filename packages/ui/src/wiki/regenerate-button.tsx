"use client";

import type { ReactNode } from "react";
import { RefreshCw } from "lucide-react";
import { Button } from "../ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";

export interface RegenerateButtonProps {
  /** Fired when the user clicks the regenerate button. Wrapper owns the
   *  mutation + toast + cache-invalidation lifecycle. */
  onRegenerate: () => void;
  /** When true, the button shows a spinning icon and is disabled (request
   *  in-flight prior to a job id being assigned). */
  isLoading?: boolean;
  /** When true, the progress dialog is open. The wrapper sets this to
   *  `true` once it receives a job id back from the regenerate mutation. */
  isInProgress?: boolean;
  /** Called when the dialog requests to close (user dismiss or job done). */
  onDialogClose?: () => void;
  /** Rendered inside the in-progress dialog — typically a job-progress
   *  widget owned by the wrapper. */
  jobSlot?: ReactNode;
}

export function RegenerateButton({
  onRegenerate,
  isLoading = false,
  isInProgress = false,
  onDialogClose,
  jobSlot,
}: RegenerateButtonProps) {
  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        onClick={onRegenerate}
        disabled={isLoading || isInProgress}
        className="h-7 gap-1.5 text-xs"
        aria-label="Regenerate this page"
      >
        <RefreshCw className={`h-3.5 w-3.5 ${isLoading ? "animate-spin" : ""}`} />
        <span className="hidden sm:inline">Regenerate</span>
      </Button>

      <Dialog
        open={isInProgress}
        onOpenChange={(v) => {
          if (!v) onDialogClose?.();
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Regenerating Page</DialogTitle>
          </DialogHeader>
          {jobSlot}
        </DialogContent>
      </Dialog>
    </>
  );
}
