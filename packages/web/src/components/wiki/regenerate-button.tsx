"use client";

import { useState } from "react";
import { toast } from "sonner";
import { useSWRConfig } from "swr";
import { RegenerateButton as RegenerateButtonShell } from "@repowise-dev/ui/wiki/regenerate-button";
import { regeneratePage } from "@/lib/api/pages";
import { GenerationProgressWrapper as GenerationProgress } from "@/components/jobs/generation-progress-wrapper";

interface Props {
  pageId: string;
  // repoId retained for parent compat; not currently consumed in the wrapper.
  repoId: string;
}

export function RegenerateButtonWrapper({ pageId }: Props) {
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
    void mutate(`/api/pages/lookup?page_id=${pageId}`);
    setJobId(null);
  }

  return (
    <RegenerateButtonShell
      onRegenerate={handleRegenerate}
      isLoading={loading}
      isInProgress={!!jobId}
      onDialogClose={() => setJobId(null)}
      jobSlot={jobId ? <GenerationProgress jobId={jobId} onDone={handleDone} /> : null}
    />
  );
}
