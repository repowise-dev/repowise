"use client";

import { useCallback, useRef, useState } from "react";
import { toast } from "sonner";
import type { RegenerateCascade } from "@repowise-dev/ui/wiki/regenerate-button";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { generateEstimate, generatePages } from "@/lib/api/repos";
import type { GenerateEstimate, GenerateSelection } from "@/lib/api/types";

// One bulk action: write every subsystem page a model has not written yet.
// The server resolves "unwritten" to the concept layer only (structural pages
// have no model path), so this is always the stubs and never the file layer.
const SELECTION: GenerateSelection = { kind: "unwritten" };

/**
 * The bulk "write the subsystem pages" flow behind the docs-header action. Owns
 * the lazily-fetched estimate (heavy, so cached per cascade and never fetched
 * per render), the cascade choice, the launch, and the active job id. The
 * shared `GenerateConfirmDialog` and `GenerationProgressWrapper` stay
 * presentational; the caller maps this state onto them.
 */
export function useBulkGenerate(repoId: string) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [cascade, setCascade] = useState<RegenerateCascade>("none");
  const [estimate, setEstimate] = useState<GenerateEstimate | null>(null);
  const [estimateLoading, setEstimateLoading] = useState(false);
  const [estimateError, setEstimateError] = useState<string | null>(null);
  const [launching, setLaunching] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);

  // Estimate cache keyed by cascade so toggling the choice back to a value
  // already priced doesn't refetch the heavy endpoint. The cascade the UI wants
  // shown, so a late response for a since-abandoned cascade never overwrites it.
  const cacheRef = useRef<Map<RegenerateCascade, GenerateEstimate>>(new Map());
  const wantedCascadeRef = useRef<RegenerateCascade>("none");

  const fetchEstimate = useCallback(
    async (which: RegenerateCascade) => {
      wantedCascadeRef.current = which;
      const cached = cacheRef.current.get(which);
      if (cached) {
        setEstimate(cached);
        setEstimateError(null);
        setEstimateLoading(false);
        return;
      }
      setEstimateLoading(true);
      setEstimateError(null);
      try {
        const res = await generateEstimate(repoId, { selection: SELECTION, cascade: which });
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
    [repoId],
  );

  const open = useCallback(() => {
    cacheRef.current.clear();
    setCascade("none");
    setEstimate(null);
    setEstimateError(null);
    void fetchEstimate("none");
    setConfirmOpen(true);
  }, [fetchEstimate]);

  const changeCascade = useCallback(
    (next: RegenerateCascade) => {
      setCascade(next);
      void fetchEstimate(next);
    },
    [fetchEstimate],
  );

  const confirm = useCallback(async () => {
    setLaunching(true);
    try {
      const res = await generatePages(repoId, { selection: SELECTION, cascade });
      setJobId(res.job_id);
      setConfirmOpen(false);
    } catch (e) {
      toast.error("Couldn't start generation", { description: toFriendlyMessage(e) });
    } finally {
      setLaunching(false);
    }
  }, [repoId, cascade]);

  const clearJob = useCallback(() => setJobId(null), []);

  const noProvider = !!estimate && estimate.provider.name == null;

  return {
    confirmOpen,
    setConfirmOpen,
    cascade,
    changeCascade,
    estimate,
    estimateLoading,
    estimateError,
    noProvider,
    launching,
    jobId,
    open,
    confirm,
    clearJob,
  };
}
