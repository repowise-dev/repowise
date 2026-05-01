"use client";

import { useState } from "react";
import { toast } from "sonner";
import { RefreshCw } from "lucide-react";
import { useSWRConfig } from "swr";
import { regeneratePage } from "@/lib/api/pages";
import { Button } from "@repowise-dev/ui/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@repowise-dev/ui/ui/dialog";
import { GenerationProgressWrapper as GenerationProgress } from "@/components/jobs/generation-progress-wrapper";

interface Props {
  pageId: string;
  repoId: string;
}

export function RegenerateButton({ pageId, repoId }: Props) {
  const { mutate } = useSWRConfig();
  const [loading, setLoading] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);

  async function handleRegenerate() {
    setLoading(true);
    try {
      const result = await regeneratePage(pageId);
      setJobId(result.job_id);
      toast.info("Regeneration queued");
    } catch (e) {
      toast.error("Failed to queue regeneration", {
        description: e instanceof Error ? e.message : undefined,
      });
    } finally {
      setLoading(false);
    }
  }

  function handleDone() {
    // Revalidate the page data so confidence badge and content refresh
    mutate(`/api/pages/lookup?page_id=${pageId}`);
    setJobId(null);
  }

  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        onClick={handleRegenerate}
        disabled={loading || !!jobId}
        className="h-7 gap-1.5 text-xs"
        aria-label="Regenerate this page"
      >
        <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
        <span className="hidden sm:inline">Regenerate</span>
      </Button>

      <Dialog open={!!jobId} onOpenChange={(v) => { if (!v) setJobId(null); }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Regenerating Page</DialogTitle>
          </DialogHeader>
          {jobId && (
            <GenerationProgress jobId={jobId} onDone={handleDone} />
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
