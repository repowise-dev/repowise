"use client";

/**
 * Dead Code view — the safe-to-delete pile (the "what do I delete" punchline),
 * optional cluster rollups behind progressive disclosure, and the full
 * drill-down table with its single confidence control. Carries the optimistic
 * row patch + Undo toast, bulk resolve, the "Propose cleanup" agent brief, and
 * Re-analyze.
 *
 * Presentation + orchestration only: the host injects data fetching,
 * mutations, links, and navigation through a {@link DeadCodeAdapter}, so web
 * and hosted render the same view from one source.
 */

import { useMemo, useState } from "react";
import useSWR from "swr";
import { toast } from "sonner";
import type {
  DeadCodeFinding,
  DeadCodeStatus,
  DeadCodeSummary,
} from "@repowise-dev/types/dead-code";

import { Button } from "../ui/button";
import { Skeleton } from "../ui/skeleton";
import { CollapsibleSection } from "../shared/collapsible-section";
import { AiPromptModal } from "../health/ai-prompt-modal";
import { buildDeadCodeAiPrompt } from "../health/ai-prompt-builder";

import { SummaryBar } from "./summary-bar";
import { SafeToDeletePile } from "./safe-to-delete-pile";
import { OwnerLeaderboard } from "./owner-leaderboard";
import { FindingsBreakdownGrid } from "./findings-breakdown-grid";
import { FindingsTable } from "./findings-table";
import type { DeadCodeAdapter } from "./dead-code-adapter";
import { toFriendlyMessage } from "../lib/errors";

/**
 * Server ceiling for one findings page (`limit` is clamped to 500 server-side).
 * Hitting it means the table shows a slice, and every count derived from that
 * slice has to say so rather than reading as the whole repository.
 */
const FINDINGS_LIMIT = 500;

/** The one failure card both fetches use, so a broken load never reads as "clean". */
function RetryCard({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-4 text-sm text-[var(--color-text-secondary)] flex items-center justify-between gap-2">
      <span>{message}</span>
      <Button size="sm" variant="outline" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}

export function DeadCodeView({ adapter }: { adapter: DeadCodeAdapter }) {
  const [analyzing, setAnalyzing] = useState(false);
  const [promptIds, setPromptIds] = useState<string[] | null>(null);
  // Optimistic row state lives here, not in the table: the pile, the cluster
  // rollups and the table all read one slice, so resolving a row (or undoing
  // it) moves every surface together.
  const [overrides, setOverrides] = useState<Record<string, DeadCodeFinding>>({});

  const {
    data: summary,
    isLoading: loadingSummary,
    error: summaryError,
    mutate: mutateSummary,
  } = useSWR<DeadCodeSummary>(
    `dead-code-summary:${adapter.cacheKey}`,
    () => adapter.getSummary(),
    { revalidateOnFocus: false },
  );

  // Single findings fetch feeds the pile, the cluster views, AND the drill-down
  // table (which filters this slice client-side) — no second fetch.
  const {
    data: findings,
    isLoading: loadingFindings,
    error: findingsError,
    mutate: mutateFindings,
  } = useSWR<DeadCodeFinding[]>(
    `dead-code-findings:${adapter.cacheKey}:all`,
    () => adapter.listFindings({ limit: FINDINGS_LIMIT }),
    { revalidateOnFocus: false },
  );

  const fetched = useMemo(() => findings ?? [], [findings]);
  // The server hands back at most FINDINGS_LIMIT rows with no total, so a full
  // page means "there may be more" and the counts below have to be scoped.
  const truncated = fetched.length >= FINDINGS_LIMIT;

  const findingsList = useMemo(
    () => fetched.map((f) => overrides[f.id] ?? f).filter((f) => f.status === "open"),
    [fetched, overrides],
  );
  const safeFindings = useMemo(
    () => findingsList.filter((f) => f.safe_to_delete),
    [findingsList],
  );

  // "Propose cleanup" opens the shared AI-prompt modal seeded with the safe
  // pile — same agent-flavor picker (incl. repowise MCP) and copy affordance as
  // every other AI action in the dashboard.
  const handlePropose = (findingIds: string[]) => setPromptIds(findingIds);

  // Seeded from the whole open slice, not just the safe pile: a per-row or
  // per-selection prompt can name any finding the table can show.
  const promptFindings = promptIds
    ? findingsList.filter((f) => promptIds.includes(f.id))
    : [];

  const handleAnalyze = async () => {
    setAnalyzing(true);
    try {
      const started = await adapter.analyze();
      const jobId = started?.job_id;
      if (jobId && adapter.waitForAnalysis) {
        toast.success("Analysis started — this page refreshes when it finishes.");
        await adapter.waitForAnalysis(jobId);
        // The pass rewrites the findings, so local optimistic state is stale.
        setOverrides({});
        await Promise.all([mutateSummary(), mutateFindings()]);
        toast.success("Dead-code findings refreshed.");
      } else {
        toast.success("Analysis started — results will appear shortly.");
      }
    } catch (err) {
      // 409 is the one failure with a specific remedy: wait for the other job.
      const status = (err as { status?: number } | null)?.status;
      toast.error(
        status === 409
          ? "Another job is already running for this repository. Try again once it finishes."
          : `Couldn't start analysis: ${toFriendlyMessage(err)}`,
      );
    } finally {
      setAnalyzing(false);
    }
  };

  // Row-level patch with optimistic undo toast; injected into the table.
  const handlePatch = async (id: string, patch: { status: DeadCodeStatus }) => {
    const finding = findingsList.find((f) => f.id === id);
    const previousStatus: DeadCodeStatus = finding?.status ?? "open";
    const updated = await adapter.patchFinding(id, patch);
    setOverrides((prev) => ({ ...prev, [id]: updated }));
    toast.success(`Finding ${patch.status.replace(/_/g, " ")}`, {
      action: {
        label: "Undo",
        onClick: async () => {
          try {
            const reverted = await adapter.patchFinding(id, { status: previousStatus });
            // Put the row back on screen; a server-confirmed revert that only
            // lands in the database is indistinguishable from a failed undo.
            setOverrides((prev) => ({ ...prev, [id]: reverted }));
          } catch (err) {
            toast.error(`Couldn't undo: ${toFriendlyMessage(err)}`);
          }
        },
      },
      duration: 6000,
    });
    return updated;
  };

  const handleBulkResolve = async (ids: string[]) => {
    const succeededIds: string[] = [];
    for (const id of ids) {
      try {
        await adapter.patchFinding(id, { status: "resolved" });
        succeededIds.push(id);
      } catch {
        // continue; report partial below
      }
    }
    // Reflect only the rows the server confirmed — never a positional guess.
    if (succeededIds.length > 0) {
      const confirmed = new Set(succeededIds);
      setOverrides((prev) => {
        const next = { ...prev };
        for (const f of findingsList) {
          if (confirmed.has(f.id)) next[f.id] = { ...f, status: "resolved" as DeadCodeStatus };
        }
        return next;
      });
    }
    const succeeded = succeededIds.length;
    if (succeeded === ids.length) {
      toast.success(`Resolved ${succeeded} finding${succeeded === 1 ? "" : "s"}`);
    } else if (succeeded > 0) {
      toast.warning(`Resolved ${succeeded} of ${ids.length}; some failed`);
    } else {
      toast.error("Couldn't resolve findings");
    }
    return succeededIds;
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-end">
        <Button size="sm" variant="outline" onClick={handleAnalyze} disabled={analyzing}>
          {analyzing ? "Starting…" : "Re-analyze"}
        </Button>
      </div>

      {loadingSummary ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full rounded-lg" />
          ))}
        </div>
      ) : summary ? (
        <SummaryBar summary={summary} />
      ) : summaryError ? (
        <RetryCard message="Couldn't load summary." onRetry={() => void mutateSummary()} />
      ) : null}

      {/* A failed findings fetch must never render as an empty repository. */}
      {findingsError && !loadingFindings && (
        <RetryCard message="Couldn't load findings." onRetry={() => void mutateFindings()} />
      )}

      {/* Act now: the single "what do I delete" surface. */}
      {findings && safeFindings.length > 0 && (
        <SafeToDeletePile
          findings={safeFindings}
          onPropose={handlePropose}
          onSelect={(f) => adapter.navigate(adapter.fileHref(f.file_path))}
          // The summary total covers the whole repo; the file and finding
          // counts beside it come from a capped slice. Only pass it when the
          // two describe the same population.
          {...(summary && !truncated ? { reclaimableLines: summary.deletable_lines } : {})}
        />
      )}

      {/* Where it clusters — optional rollups, collapsed by default so the spine
          stays pile → drill-down. */}
      {findingsList.length > 0 && (
        <CollapsibleSection
          title="Where it clusters"
          hint="By owner and confidence × kind"
          defaultOpen={false}
        >
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <div>
              <p className="mb-2 text-xs text-[var(--color-text-tertiary)]">
                Reclaimable lines per primary contributor — who has the most cleanup leverage.
              </p>
              <OwnerLeaderboard findings={findingsList} safeOnly />
            </div>
            <div>
              <p className="mb-2 text-xs text-[var(--color-text-tertiary)]">
                Where findings concentrate — start with high-confidence cells.
              </p>
              <FindingsBreakdownGrid findings={findingsList} />
            </div>
          </div>
        </CollapsibleSection>
      )}

      {/* Drill-down: the full interactive table, the single confidence control. */}
      <CollapsibleSection
        title="All findings"
        hint={
          loadingFindings
            ? "Loading…"
            : truncated && summary
              ? `Showing ${findingsList.length} of ${summary.total_findings} findings`
              : `${findingsList.length} findings`
        }
        defaultOpen={false}
      >
        <FindingsTable
          findings={findingsList}
          repoId={adapter.repoId}
          onPatch={handlePatch}
          onBulkResolve={handleBulkResolve}
          isLoading={loadingFindings}
        />
      </CollapsibleSection>

      <AiPromptModal
        open={promptIds !== null}
        onOpenChange={(o) => !o && setPromptIds(null)}
        getPrompt={
          promptFindings.length > 0
            ? (flavor) =>
                buildDeadCodeAiPrompt({
                  findings: promptFindings.map((f) => ({
                    file_path: f.file_path,
                    symbol_name: f.symbol_name,
                    kind: f.kind,
                    reason: f.reason,
                    lines: f.lines,
                    confidence: f.confidence,
                    risk_factors: f.risk_factors ?? null,
                  })),
                  flavor,
                })
            : null
        }
        title="AI cleanup prompt"
        description="A ready-to-paste prompt that has your AI agent verify and remove this dead-code pile safely, in reviewable commits."
      />
    </div>
  );
}
