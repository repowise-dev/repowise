"use client";

import { useState } from "react";
import { toast } from "sonner";
import useSWR, { useSWRConfig } from "swr";
import { RegenerateButton as RegenerateButtonShell } from "@repowise-dev/ui/wiki/regenerate-button";
import { ConfirmDialog } from "@repowise-dev/ui/ui/confirm-dialog";
import { formatTokens } from "@repowise-dev/ui/lib/format";
import { getPageById, regeneratePage } from "@/lib/api/pages";
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
  const [confirmOpen, setConfirmOpen] = useState(false);

  // Token history for the cost estimate in the confirm dialog. Same SWR key
  // the wiki page uses, so this is usually already cached.
  const { data: page } = useSWR(
    `/api/pages/lookup?page_id=${pageId}`,
    () => getPageById(pageId),
    { revalidateOnFocus: false },
  );

  const estimate =
    page && (page.input_tokens > 0 || page.output_tokens > 0)
      ? `Based on the last generation this will spend roughly ${formatTokens(
          page.input_tokens,
        )} input + ${formatTokens(page.output_tokens)} output tokens` +
        (page.provider_name ? ` on ${page.provider_name}` : "") +
        "."
      : "Token cost depends on the page's sources; no previous generation is recorded to estimate from.";

  async function handleRegenerate() {
    setConfirmOpen(false);
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
    <>
      <RegenerateButtonShell
        onRegenerate={() => setConfirmOpen(true)}
        isLoading={loading}
        isInProgress={!!jobId}
        onDialogClose={() => setJobId(null)}
        jobSlot={jobId ? <GenerationProgress jobId={jobId} onDone={handleDone} /> : null}
      />
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="Regenerate this page?"
        description={`The current content is archived as a version and replaced. ${estimate}`}
        confirmLabel="Regenerate"
        destructive={false}
        loading={loading}
        onConfirm={() => void handleRegenerate()}
      />
    </>
  );
}
