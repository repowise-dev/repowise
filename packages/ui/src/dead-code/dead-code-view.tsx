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

import { useState } from "react";
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

export function DeadCodeView({ adapter }: { adapter: DeadCodeAdapter }) {
  const [analyzing, setAnalyzing] = useState(false);
  const [promptIds, setPromptIds] = useState<string[] | null>(null);

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
  const { data: findings, isLoading: loadingFindings } = useSWR<DeadCodeFinding[]>(
    `dead-code-findings:${adapter.cacheKey}:all`,
    () => adapter.listFindings({ limit: 500 }),
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
      await adapter.analyze();
      toast.success("Analysis started — results will appear shortly.");
    } catch (err) {
      toast.error(
        err instanceof Error
          ? `Couldn't start analysis: ${err.message}`
          : "Couldn't start analysis",
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
    toast.success(`Finding ${patch.status.replace(/_/g, " ")}`, {
      action: {
        label: "Undo",
        onClick: async () => {
          try {
            await adapter.patchFinding(id, { status: previousStatus });
          } catch (err) {
            toast.error(
              err instanceof Error ? `Couldn't undo: ${err.message}` : "Couldn't undo",
            );
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
          onPropose={handlePropose}
          onSelect={(f) => adapter.navigate(adapter.fileHref(f.file_path))}
          {...(summary ? { reclaimableLines: summary.deletable_lines } : {})}
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
