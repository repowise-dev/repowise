"use client";

import { useCallback, useRef, useState } from "react";
import { toast } from "sonner";
import type { RegenerateCascade } from "@repowise-dev/ui/wiki/regenerate-button";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { generateEstimate, generatePages } from "@/lib/api/repos";
import type { GenerateEstimate, GenerateSelection } from "@/lib/api/types";

/**
 * The bulk "Write with AI" flow, shared by the dashboard quick action, the
 * auto-docs banner, and the coverage "Write selected" action. Owns the
 * lazily-fetched, per-cascade cached `/generate/estimate` (heavy, so never per
 * render), the launch, and the active job id. The shared `GenerateConfirmDialog`
 * and `GenerationProgressWrapper` stay presentational; the caller maps this
 * state onto them and renders progress wherever it fits.
 */
export function useBulkGenerate(repoId: string) {
  const [selection, setSelection] = useState<GenerateSelection | null>(null);
  const [label, setLabel] = useState<string | undefined>(undefined);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [cascade, setCascade] = useState<RegenerateCascade>("none");
  const [estimate, setEstimate] = useState<GenerateEstimate | null>(null);
  const [estimateLoading, setEstimateLoading] = useState(false);
  const [estimateError, setEstimateError] = useState<string | null>(null);
  const [launching, setLaunching] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);

  // Estimate cache keyed by cascade so toggling the choice back doesn't refetch
  // the heavy endpoint. Cleared on each new `begin`, since the selection (and
  // thus the estimate) changes.
  const cacheRef = useRef<Map<RegenerateCascade, GenerateEstimate>>(new Map());
  const wantedCascadeRef = useRef<RegenerateCascade>("none");
  const selectionRef = useRef<GenerateSelection | null>(null);
  // A monotonic token bumped on each `begin`. Latest-wins keys on BOTH the
  // token and the cascade, so a late estimate for a since-abandoned selection
  // (not just a since-abandoned cascade) can never write into the current
  // dialog or its freshly-cleared cache.
  const requestRef = useRef(0);

  const fetchEstimate = useCallback(
    async (which: RegenerateCascade) => {
      const sel = selectionRef.current;
      if (!sel) return;
      const token = requestRef.current;
      wantedCascadeRef.current = which;
      const cached = cacheRef.current.get(which);
      if (cached) {
        setEstimate(cached);
        setEstimateError(null);
        return;
      }
      setEstimateLoading(true);
      setEstimateError(null);
      const current = () =>
        requestRef.current === token && wantedCascadeRef.current === which;
      try {
        const res = await generateEstimate(repoId, { selection: sel, cascade: which });
        if (requestRef.current !== token) return;
        cacheRef.current.set(which, res);
        if (current()) setEstimate(res);
      } catch (e) {
        if (current()) {
          setEstimateError(toFriendlyMessage(e));
          setEstimate(null);
        }
      } finally {
        if (current()) setEstimateLoading(false);
      }
    },
    [repoId],
  );

  /** Open the confirm dialog for a selection. The estimate is fetched lazily
   *  here (once, on open) and re-priced per cascade choice — never live as a
   *  selection is built, since the endpoint is heavy. */
  const begin = useCallback(
    (
      sel: GenerateSelection,
      opts?: { label?: string; defaultCascade?: RegenerateCascade },
    ) => {
      const startCascade = opts?.defaultCascade ?? "none";
      requestRef.current += 1;
      cacheRef.current.clear();
      selectionRef.current = sel;
      setSelection(sel);
      setLabel(opts?.label);
      setCascade(startCascade);
      setEstimateError(null);
      setEstimate(null);
      void fetchEstimate(startCascade);
      setConfirmOpen(true);
    },
    [fetchEstimate],
  );

  const changeCascade = useCallback(
    (next: RegenerateCascade) => {
      setCascade(next);
      void fetchEstimate(next);
    },
    [fetchEstimate],
  );

  const confirm = useCallback(async () => {
    if (!selection) return;
    setLaunching(true);
    try {
      const res = await generatePages(repoId, { selection, cascade });
      setJobId(res.job_id);
      setConfirmOpen(false);
    } catch (e) {
      toast.error("Couldn't start generation", { description: toFriendlyMessage(e) });
    } finally {
      setLaunching(false);
    }
  }, [repoId, selection, cascade]);

  const clearJob = useCallback(() => setJobId(null), []);

  const noProvider = !!estimate && estimate.provider.name == null;

  return {
    selection,
    label,
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
    begin,
    confirm,
    clearJob,
  };
}
