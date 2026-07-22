"use client";

import { useCallback, useRef, useState } from "react";
import { toast } from "sonner";
import {
  RegenerateButton,
  GenerateConfirmDialog,
  type RegenerateCascade,
} from "@repowise-dev/ui/wiki/regenerate-button";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { GenerationProgressWrapper } from "@/components/jobs/generation-progress-wrapper";
import { regeneratePage } from "@/lib/api/pages";
import { generateEstimate } from "@/lib/api/repos";
import { formatEstimateCost } from "@/lib/generate-format";
import type { PageResponse, GenerateEstimate } from "@/lib/api/types";

/**
 * Reader-side "Write with AI" / "Regenerate" for a single page. Owns the
 * lazily-fetched cost estimate, the cascade choice, the launch, and the live
 * progress — the shared `ui` components stay presentational.
 */
export function PageGenerateButton({
  page,
  repoId,
  onGenerated,
  variant = "button",
}: {
  page: PageResponse;
  repoId: string;
  /** Called once a run completes so the host can refresh the page list. */
  onGenerated?: () => void;
  /** "button" is the header toolbar affordance; "inline" is the compact,
   *  link-like entry point rendered beside the provenance pill in the reader. */
  variant?: "button" | "inline";
}) {
  const mode = page.is_deterministic ? "write" : "regenerate";
  const settingsHref = `/repos/${repoId}/settings#provider`;

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [cascade, setCascade] = useState<RegenerateCascade>("none");
  const [estimate, setEstimate] = useState<GenerateEstimate | null>(null);
  const [estimateLoading, setEstimateLoading] = useState(false);
  const [estimateError, setEstimateError] = useState<string | null>(null);
  const [launching, setLaunching] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);

  // Cache estimates per cascade so toggling the choice back doesn't refetch the
  // heavy /generate/estimate endpoint.
  const cacheRef = useRef<Map<RegenerateCascade, GenerateEstimate>>(new Map());
  // The cascade whose estimate we currently want shown. A slow response for a
  // since-abandoned cascade must not overwrite the displayed one (latest wins).
  const wantedCascadeRef = useRef<RegenerateCascade>("none");

  const fetchEstimate = useCallback(
    async (which: RegenerateCascade) => {
      wantedCascadeRef.current = which;
      const cached = cacheRef.current.get(which);
      if (cached) {
        setEstimate(cached);
        setEstimateError(null);
        return;
      }
      setEstimateLoading(true);
      setEstimateError(null);
      try {
        const res = await generateEstimate(repoId, {
          selection: { kind: "page_ids", page_ids: [page.id] },
          cascade: which,
        });
        cacheRef.current.set(which, res);
        if (wantedCascadeRef.current === which) setEstimate(res);
      } catch (e) {
        if (wantedCascadeRef.current === which) {
          setEstimateError(toFriendlyMessage(e));
          setEstimate(null);
        }
      } finally {
        if (wantedCascadeRef.current === which) setEstimateLoading(false);
      }
    },
    [repoId, page.id],
  );

  function openConfirm() {
    cacheRef.current.clear();
    setEstimate(null);
    setEstimateError(null);
    setConfirmOpen(true);
    void fetchEstimate(cascade);
  }

  function changeCascade(next: RegenerateCascade) {
    setCascade(next);
    void fetchEstimate(next);
  }

  async function confirm() {
    setLaunching(true);
    try {
      const res = await regeneratePage(page.id, { cascade });
      setJobId(res.job_id);
      setConfirmOpen(false);
    } catch (e) {
      toast.error("Couldn't start generation", { description: toFriendlyMessage(e) });
    } finally {
      setLaunching(false);
    }
  }

  const noProvider = !!estimate && estimate.provider.name == null;

  return (
    <>
      <RegenerateButton
        mode={mode}
        inline={variant === "inline"}
        onRegenerate={openConfirm}
        isLoading={launching && !jobId}
        isInProgress={jobId != null}
        onDialogClose={() => setJobId(null)}
        jobSlot={
          jobId != null ? (
            <GenerationProgressWrapper
              jobId={jobId}
              onDone={() => onGenerated?.()}
            />
          ) : null
        }
      />

      <GenerateConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        mode={mode}
        pageTitle={page.title}
        cascade={cascade}
        onCascadeChange={changeCascade}
        estimate={
          estimate && !noProvider
            ? {
                totalPages: estimate.total_pages,
                costText: formatEstimateCost(estimate.estimate),
                staleCount: estimate.pages_to_mark_stale,
              }
            : null
        }
        estimateLoading={estimateLoading}
        estimateError={estimateError}
        noProvider={noProvider}
        settingsHref={settingsHref}
        onConfirm={confirm}
        launching={launching}
      />
    </>
  );
}
