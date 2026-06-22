"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { toast } from "sonner";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import { Button } from "@repowise-dev/ui/ui/button";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { CollapsibleSection } from "@repowise-dev/ui/shared/collapsible-section";
import { SummaryBar } from "@repowise-dev/ui/dead-code/summary-bar";
import { SafeToDeletePile } from "@repowise-dev/ui/dead-code/safe-to-delete-pile";
import { OwnerLeaderboard } from "@repowise-dev/ui/dead-code/owner-leaderboard";
import { FindingsBreakdownGrid } from "@repowise-dev/ui/dead-code/findings-breakdown-grid";
import { FindingsTable } from "@repowise-dev/ui/dead-code/findings-table";
import { AiPromptModal, buildDeadCodeAiPrompt } from "@repowise-dev/ui/health";
import { getDeadCodeSummary, listDeadCode, analyzeDeadCode, patchDeadCodeFinding } from "@/lib/api/dead-code";
import type { DeadCodeFindingResponse, DeadCodeSummaryResponse } from "@/lib/api/types";
import type { DeadCodeStatus } from "@repowise-dev/types/dead-code";

export function DeadCodeTab({ repoId }: { repoId: string }) {
  const router = useRouter();
  const [analyzing, setAnalyzing] = useState(false);
  const [promptIds, setPromptIds] = useState<string[] | null>(null);

  const { data: summary, isLoading: loadingSummary, error: summaryError, mutate: mutateSummary } =
    useSWR<DeadCodeSummaryResponse>(
      `dead-code-summary:${repoId}`,
      () => getDeadCodeSummary(repoId),
      { revalidateOnFocus: false },
    );

  // Single findings fetch feeds the pile, the cluster views, AND the drill-down
  // table (which filters this slice client-side) — no second fetch.
  const { data: findings, isLoading: loadingFindings } = useSWR<DeadCodeFindingResponse[]>(
    `dead-code-findings:${repoId}:all`,
    () => listDeadCode(repoId, { limit: 500 }),
    { revalidateOnFocus: false },
  );

  const findingsList = findings ?? [];
  const safeFindings = findingsList.filter((f) => f.safe_to_delete);

  // "Propose cleanup" opens the shared AI-prompt modal seeded with the safe
  // pile — same agent-flavor picker (incl. repowise MCP) and copy affordance as
  // every other AI action in the dashboard.
  const handlePropose = (findingIds: string[]) => setPromptIds(findingIds);

  const promptFindings = promptIds
    ? safeFindings.filter((f) => promptIds.includes(f.id))
    : [];

  const handleAnalyze = async () => {
    setAnalyzing(true);
    try {
      await analyzeDeadCode(repoId);
      toast.success("Analysis started — results will appear shortly.");
    } catch (err) {
      toast.error(
        err instanceof Error ? `Couldn't start analysis: ${err.message}` : "Couldn't start analysis",
      );
    } finally {
      setAnalyzing(false);
    }
  };

  // Row-level patch with optimistic undo toast; injected into the ui table.
  const handlePatch = async (id: string, patch: { status: DeadCodeStatus }) => {
    const finding = findingsList.find((f) => f.id === id);
    const previousStatus: DeadCodeStatus = finding?.status ?? "open";
    const updated = await patchDeadCodeFinding(id, patch);
    toast.success(`Finding ${patch.status.replace(/_/g, " ")}`, {
      action: {
        label: "Undo",
        onClick: async () => {
          try {
            await patchDeadCodeFinding(id, { status: previousStatus });
          } catch (err) {
            toast.error(err instanceof Error ? `Couldn't undo: ${err.message}` : "Couldn't undo");
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
        await patchDeadCodeFinding(id, { status: "resolved" });
        succeededIds.push(id);
      } catch {
        // continue; report partial below
      }
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
        <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-4 text-sm text-[var(--color-text-secondary)] flex items-center justify-between gap-2">
          <span>Couldn&apos;t load summary.</span>
          <Button size="sm" variant="outline" onClick={() => mutateSummary()}>
            Retry
          </Button>
        </div>
      ) : null}

      {/* Act now: the single "what do I delete" surface. */}
      {findings && safeFindings.length > 0 && (
        <SafeToDeletePile
          findings={safeFindings}
          reclaimableLines={summary?.deletable_lines}
          onPropose={handlePropose}
          onSelect={(f) => router.push(fileEntityPath(`/repos/${repoId}`, f.file_path))}
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
        hint={`${findingsList.length} findings`}
        defaultOpen={false}
      >
        <FindingsTable
          findings={findingsList}
          repoId={repoId}
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
                    risk_factors: f.risk_factors,
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
