"use client";

import { useCallback, useRef, useState } from "react";
import { toast } from "sonner";
import type { RegenerateCascade } from "@repowise-dev/ui/wiki/regenerate-button";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { generateEstimate, generatePages } from "@/lib/api/repos";
import type { GenerateEstimate, GenerateSelection } from "@/lib/api/types";

/** Ranked coverage buckets offered in the bulk confirm, mirroring init's
 *  chooser: 10 / 20 / 30 / 50 / All, recommended 20%. `1` is "everything". */
export const COVERAGE_OPTIONS = [0.1, 0.2, 0.3, 0.5, 1] as const;
export const RECOMMENDED_COVERAGE = 0.2;

/**
 * The bulk "Write with AI" flow, shared by the dashboard quick action, the
 * auto-docs banner, and the coverage "Write selected" action. Owns the
 * lazily-fetched estimate (heavy, so never per render) cached per coverage
 * bucket and cascade, the launch, and the active job id. The shared
 * `GenerateConfirmDialog` and `GenerationProgressWrapper` stay presentational;
 * the caller maps this state onto them and renders progress wherever it fits.
 *
 * A selection is either explicit (a fixed `GenerateSelection`) or a ranked
 * coverage pick. Begin a coverage-mode flow with `beginCoverage` — the dialog
 * then shows the bucket picker and the estimate re-prices the real page count
 * for each bucket the user tries.
 */
export function useBulkGenerate(repoId: string) {
  const [selection, setSelection] = useState<GenerateSelection | null>(null);
  const [label, setLabel] = useState<string | undefined>(undefined);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [cascade, setCascade] = useState<RegenerateCascade>("none");
  // null when the flow is an explicit selection; a fraction when it is a ranked
  // coverage pick (which reveals the bucket picker in the dialog).
  const [coveragePct, setCoveragePct] = useState<number | null>(null);
  const [estimate, setEstimate] = useState<GenerateEstimate | null>(null);
  const [estimateLoading, setEstimateLoading] = useState(false);
  const [estimateError, setEstimateError] = useState<string | null>(null);
  const [launching, setLaunching] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);

  // Estimate cache keyed by (coverage bucket, cascade) so toggling either back
  // to a value already priced doesn't refetch the heavy endpoint. Cleared on
  // each new `begin`, since the base selection (and thus the estimate) changes.
  const cacheRef = useRef<Map<string, GenerateEstimate>>(new Map());
  // The (bucket, cascade) the UI currently wants shown, so a late response for a
  // since-abandoned bucket or cascade never writes into the dialog.
  const wantedKeyRef = useRef("");
  const explicitSelectionRef = useRef<GenerateSelection | null>(null);
  // A monotonic token bumped on each `begin`. Latest-wins keys on BOTH the token
  // and the wanted key, so a late estimate for a since-abandoned selection (not
  // just a since-abandoned bucket/cascade) can never write into the current
  // dialog or its freshly-cleared cache.
  const requestRef = useRef(0);

  const keyFor = (pct: number | null, which: RegenerateCascade) =>
    `${pct ?? "x"}|${which}`;

  // The selection sent to the estimate/launch endpoints for a given bucket:
  // ranked when in coverage mode, the fixed explicit selection otherwise.
  const selectionFor = useCallback(
    (pct: number | null): GenerateSelection | null =>
      pct != null ? { kind: "ranked", coverage_pct: pct } : explicitSelectionRef.current,
    [],
  );

  const fetchEstimate = useCallback(
    async (pct: number | null, which: RegenerateCascade) => {
      const sel = selectionFor(pct);
      if (!sel) return;
      const token = requestRef.current;
      const key = keyFor(pct, which);
      wantedKeyRef.current = key;
      const cached = cacheRef.current.get(key);
      if (cached) {
        setEstimate(cached);
        setEstimateError(null);
        // Clear any spinner left set by a still-in-flight fetch for a bucket the
        // user has since switched away from: that fetch's `finally` no-ops now
        // (current() is false), so this cache hit owns resetting the flag.
        setEstimateLoading(false);
        return;
      }
      setEstimateLoading(true);
      setEstimateError(null);
      const current = () =>
        requestRef.current === token && wantedKeyRef.current === key;
      try {
        const res = await generateEstimate(repoId, { selection: sel, cascade: which });
        if (requestRef.current !== token) return;
        cacheRef.current.set(key, res);
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
    [repoId, selectionFor],
  );

  const openFor = useCallback(
    (
      pct: number | null,
      explicit: GenerateSelection | null,
      opts?: { label?: string; defaultCascade?: RegenerateCascade },
    ) => {
      const startCascade = opts?.defaultCascade ?? "none";
      requestRef.current += 1;
      cacheRef.current.clear();
      explicitSelectionRef.current = explicit;
      setSelection(explicit ?? (pct != null ? { kind: "ranked", coverage_pct: pct } : null));
      setCoveragePct(pct);
      setLabel(opts?.label);
      setCascade(startCascade);
      setEstimateError(null);
      setEstimate(null);
      void fetchEstimate(pct, startCascade);
      setConfirmOpen(true);
    },
    [fetchEstimate],
  );

  /** Open the confirm dialog for a fixed, explicit selection (no coverage
   *  picker). The estimate is fetched lazily here and re-priced per cascade. */
  const begin = useCallback(
    (
      sel: GenerateSelection,
      opts?: { label?: string; defaultCascade?: RegenerateCascade },
    ) => openFor(null, sel, opts),
    [openFor],
  );

  /** Open the confirm dialog in ranked coverage mode, starting at
   *  `startPct` (default the recommended bucket). The dialog shows the bucket
   *  picker and re-prices the real page count for each bucket tried. */
  const beginCoverage = useCallback(
    (opts?: { label?: string; startPct?: number; defaultCascade?: RegenerateCascade }) =>
      openFor(opts?.startPct ?? RECOMMENDED_COVERAGE, null, opts),
    [openFor],
  );

  const changeCascade = useCallback(
    (next: RegenerateCascade) => {
      setCascade(next);
      void fetchEstimate(coveragePct, next);
    },
    [fetchEstimate, coveragePct],
  );

  const changeCoverage = useCallback(
    (pct: number) => {
      setCoveragePct(pct);
      setSelection({ kind: "ranked", coverage_pct: pct });
      void fetchEstimate(pct, cascade);
    },
    [fetchEstimate, cascade],
  );

  const confirm = useCallback(async () => {
    const sel = selectionFor(coveragePct);
    if (!sel) return;
    setLaunching(true);
    try {
      const res = await generatePages(repoId, { selection: sel, cascade });
      setJobId(res.job_id);
      setConfirmOpen(false);
    } catch (e) {
      toast.error("Couldn't start generation", { description: toFriendlyMessage(e) });
    } finally {
      setLaunching(false);
    }
  }, [repoId, selectionFor, coveragePct, cascade]);

  const clearJob = useCallback(() => setJobId(null), []);

  const noProvider = !!estimate && estimate.provider.name == null;

  return {
    selection,
    label,
    confirmOpen,
    setConfirmOpen,
    cascade,
    changeCascade,
    coveragePct,
    changeCoverage,
    estimate,
    estimateLoading,
    estimateError,
    noProvider,
    launching,
    jobId,
    begin,
    beginCoverage,
    confirm,
    clearJob,
  };
}
