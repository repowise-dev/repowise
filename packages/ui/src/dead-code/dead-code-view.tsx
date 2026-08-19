"use client";

/**
 * Dead Code view, on the section design language.
 *
 * The shape is: a lede that leads with the reclaimable line count and says in
 * prose what confidence means, then the safe-to-delete pile, the optional
 * cluster rollups, and the full drill-down table. Carries the optimistic row
 * patch + Undo toast, bulk resolve, the "Propose cleanup" agent brief, and
 * Re-analyze.
 *
 * What it replaces: four `MetricCard`s in a grid (two of them holding a stacked
 * count list inside the value slot), a red gradient hero card that repeated the
 * headline figure, and a floating chrome row carrying a status dropdown and a
 * button above all of it. The controls now sit in the header of the section
 * they operate on, which is also why the status filter can live there safely:
 * a section header renders whether or not the table underneath it does.
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

import { Trash2 } from "lucide-react";

import { Button } from "../ui/button";
import { Skeleton } from "../ui/skeleton";
import { ApiError } from "../shared/api-error";
import { EmptyState } from "../shared/empty-state";
import { OverviewSection } from "../overview/section";
import { AiPromptModal } from "../health/ai-prompt-modal";
import { buildDeadCodeAiPrompt } from "../health/ai-prompt-builder";

import { DeadCodeLede } from "./dead-code-lede";
import { SafeToDeletePile } from "./safe-to-delete-pile";
import { OwnerLeaderboard } from "./owner-leaderboard";
import { FindingsBreakdownGrid } from "./findings-breakdown-grid";
import { FindingsTable } from "./findings-table";
import { DEAD_CODE_STATUS_LABELS } from "./finding-cells";
import type { DeadCodeAdapter } from "./dead-code-adapter";
import { toFriendlyMessage } from "../lib/errors";

/**
 * Server ceiling for one findings page (`limit` is clamped to 500 server-side).
 * Hitting it means the table shows a slice, and every count derived from that
 * slice has to say so rather than reading as the whole repository.
 */
const FINDINGS_LIMIT = 500;

/** Order of the status filter; "open" first because it is the working list. */
const STATUS_ORDER: DeadCodeStatus[] = ["open", "acknowledged", "resolved", "false_positive"];

/** The one failure card both fetches use, so a broken load never reads as "clean". */
function RetryCard({
  title,
  error,
  onRetry,
}: {
  title: string;
  error: unknown;
  onRetry: () => void;
}) {
  return <ApiError title={title} message={toFriendlyMessage(error)} onRetry={onRetry} />;
}

export function DeadCodeView({ adapter }: { adapter: DeadCodeAdapter }) {
  const [analyzing, setAnalyzing] = useState(false);
  const [promptIds, setPromptIds] = useState<string[] | null>(null);
  /** Which slice the drill-down table shows; everything above it stays open-only. */
  const [statusFilter, setStatusFilter] = useState<DeadCodeStatus>("open");
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

  // Reviewing an already-actioned finding is a second, narrower question than
  // "what can I delete", so it gets its own fetch and leaves the pile, the
  // rollups and the summary reading the open slice they have always read.
  const {
    data: reviewFindings,
    isLoading: loadingReview,
    error: reviewError,
    mutate: mutateReview,
  } = useSWR<DeadCodeFinding[]>(
    statusFilter === "open"
      ? null
      : `dead-code-findings:${adapter.cacheKey}:${statusFilter}`,
    () => adapter.listFindings({ limit: FINDINGS_LIMIT, status: statusFilter }),
    { revalidateOnFocus: false },
  );

  const fetched = useMemo(() => findings ?? [], [findings]);
  // The server hands back at most FINDINGS_LIMIT rows with no total, so a full
  // page means "there may be more" and the counts below have to be scoped.
  const truncated = fetched.length >= FINDINGS_LIMIT;

  const findingsList = useMemo(() => {
    const inPayload = new Set(fetched.map((f) => f.id));
    // A finding reopened from the review list is not in the open payload, but
    // it is open now and belongs in the pile and the rollups. Refetching
    // instead would race the optimistic override that is masking it.
    const reopened = Object.values(overrides).filter((f) => !inPayload.has(f.id));
    return [...fetched.map((f) => overrides[f.id] ?? f), ...reopened].filter(
      (f) => f.status === "open",
    );
  }, [fetched, overrides]);
  const safeFindings = useMemo(
    () => findingsList.filter((f) => f.safe_to_delete),
    [findingsList],
  );

  // What the drill-down table renders. Same override + status treatment as the
  // open slice, so reopening a row drops it out of the review list immediately.
  const tableFindings = useMemo(() => {
    if (statusFilter === "open") return findingsList;
    return (reviewFindings ?? [])
      .map((f) => overrides[f.id] ?? f)
      .filter((f) => f.status === statusFilter);
  }, [statusFilter, findingsList, reviewFindings, overrides]);

  const tableLoading = statusFilter === "open" ? loadingFindings : loadingReview;
  const tableError = statusFilter === "open" ? findingsError : reviewError;
  const retryTable = () => void (statusFilter === "open" ? mutateFindings() : mutateReview());
  // The review fetch is capped the same way, and there is no server-side total
  // for a non-open status, so the hint can only say the count is a first page.
  const reviewTruncated = (reviewFindings?.length ?? 0) >= FINDINGS_LIMIT;

  // "Propose cleanup" opens the shared AI-prompt modal seeded with the safe
  // pile — same agent-flavor picker (incl. repowise MCP) and copy affordance as
  // every other AI action in the dashboard.
  const handlePropose = (findingIds: string[]) => setPromptIds(findingIds);

  // Seeded from both slices: the pile's CTA names open findings even while the
  // table is showing a review slice, and an empty modal is worse than none.
  const promptFindings = useMemo(() => {
    if (!promptIds) return [];
    const pool = new Map<string, DeadCodeFinding>();
    for (const f of [...findingsList, ...tableFindings]) pool.set(f.id, f);
    return promptIds.flatMap((id) => {
      const f = pool.get(id);
      return f ? [f] : [];
    });
  }, [promptIds, findingsList, tableFindings]);

  const handleAnalyze = async () => {
    // Guard here rather than on each button: the empty-state action cannot
    // disable itself, and two clicks would race a second job into a 409.
    if (analyzing) return;
    setAnalyzing(true);
    let jobId: string | undefined;
    try {
      const started = await adapter.analyze();
      jobId = started?.job_id;
      toast.success(
        jobId && adapter.waitForAnalysis
          ? "Analysis started — this page refreshes when it finishes."
          : "Analysis started — results will appear shortly.",
      );
    } catch (err) {
      // 409 is the one failure with a specific remedy: wait for the other job.
      const status = (err as { status?: number } | null)?.status;
      toast.error(
        status === 409
          ? "Another job is already running for this repository. Try again once it finishes."
          : `Couldn't start analysis: ${toFriendlyMessage(err)}`,
      );
      setAnalyzing(false);
      return;
    }

    // Separate from the launch above: the job is running now, so a failure
    // past this point is a failure to *watch* it, not to start it, and saying
    // "couldn't start analysis" about a live job would be wrong.
    if (!jobId || !adapter.waitForAnalysis) {
      setAnalyzing(false);
      return;
    }
    try {
      await adapter.waitForAnalysis(jobId);
      // The pass rewrites the findings, so local optimistic state is stale.
      setOverrides({});
      await Promise.all([mutateSummary(), mutateFindings(), mutateReview()]);
      toast.success("Dead-code findings refreshed.");
    } catch (err) {
      toast.error(
        `Analysis is running, but this page stopped tracking it: ${toFriendlyMessage(err)}`,
      );
    } finally {
      setAnalyzing(false);
    }
  };

  // Row-level patch with optimistic undo toast; injected into the table.
  const handlePatch = async (id: string, patch: { status: DeadCodeStatus }) => {
    // Look in the slice on screen: reopening acts on the review list,
    // resolving on the open one.
    const finding = tableFindings.find((f) => f.id === id);
    const previousStatus: DeadCodeStatus = finding?.status ?? "open";
    const updated = await adapter.patchFinding(id, patch);
    setOverrides((prev) => ({ ...prev, [id]: updated }));
    // The tiles are server-derived counts of the open slice; without this they
    // drift by one on every row action and quietly disagree with the table.
    void mutateSummary();
    toast.success(`Finding ${patch.status.replace(/_/g, " ")}`, {
      action: {
        label: "Undo",
        onClick: async () => {
          try {
            const reverted = await adapter.patchFinding(id, { status: previousStatus });
            // Put the row back on screen; a server-confirmed revert that only
            // lands in the database is indistinguishable from a failed undo.
            setOverrides((prev) => ({ ...prev, [id]: reverted }));
            void mutateSummary();
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
        for (const f of tableFindings) {
          if (confirmed.has(f.id)) next[f.id] = { ...f, status: "resolved" as DeadCodeStatus };
        }
        return next;
      });
    }
    if (succeededIds.length > 0) void mutateSummary();
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

  // The tab's one action, placed under the lede prose. It is the same element
  // wherever the lede cannot render, so a failed summary still leaves a way to
  // re-run the pass.
  const reanalyze = (
    <Button size="sm" variant="outline" onClick={handleAnalyze} disabled={analyzing}>
      {analyzing ? "Analyzing…" : "Re-analyze"}
    </Button>
  );

  return (
    <div className="flex flex-col gap-6 sm:gap-8">
      {loadingSummary ? (
        // Shapes and widths match the lede, so nothing reflows when it lands.
        <div className="flex flex-col gap-6">
          <div className="flex flex-col gap-5 lg:flex-row lg:gap-12">
            <Skeleton className="h-[92px] w-full rounded-lg lg:w-[220px]" />
            <Skeleton className="h-[92px] w-full max-w-[62ch] rounded-lg" />
          </div>
          <Skeleton className="h-[74px] w-full" />
        </div>
      ) : summary ? (
        <DeadCodeLede
          summary={summary}
          shownCount={findingsList.length}
          truncated={truncated}
          action={reanalyze}
        />
      ) : summaryError ? (
        <div className="flex flex-col gap-3">
          <RetryCard
            title="Couldn't load summary"
            error={summaryError}
            onRetry={() => void mutateSummary()}
          />
          <div>{reanalyze}</div>
        </div>
      ) : null}

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

      {/* Where it clusters. No longer behind a disclosure: the accordion existed
          because this sat among six boxes of equal weight and something had to
          give, and a hairline plus vertical rhythm now does that job without
          hiding a rollup the reader has to guess at. */}
      {findingsList.length > 0 && (
        <OverviewSection
          title="Where it clusters"
          description="The same findings cut two ways: by the person who last owned the code, and by how sure we are against how it was found."
        >
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <div className="flex flex-col gap-2">
              <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                Reclaimable lines by owner
              </p>
              <OwnerLeaderboard findings={findingsList} safeOnly />
            </div>
            <div className="flex flex-col gap-2">
              <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                Confidence against kind
              </p>
              <FindingsBreakdownGrid findings={findingsList} />
            </div>
          </div>
        </OverviewSection>
      )}

      {/* The drill-down. Its contents route off the slice actually on screen,
          not the open fetch: a failed open fetch used to blank the table even
          when the user had switched the filter to a slice that loaded fine.

          The status control lives in this header rather than among the table's
          own filters because a clean repository swaps the table out for an
          empty state, and a control living inside it would go with it. The
          header renders either way. */}
      <OverviewSection
        title="All findings"
        description={
          tableLoading
            ? "Loading…"
            : statusFilter !== "open"
              ? `${reviewTruncated ? `The first ${tableFindings.length}` : tableFindings.length} ${DEAD_CODE_STATUS_LABELS[statusFilter].toLowerCase()} finding${tableFindings.length === 1 ? "" : "s"}. Reopen one to put it back in the working list.`
              : truncated && summary
                ? `Showing ${findingsList.length} of ${summary.total_findings} open findings. Resolve, acknowledge or flag a false positive; every action is undoable for six seconds.`
                : `${findingsList.length} open finding${findingsList.length === 1 ? "" : "s"}. Resolve, acknowledge or flag a false positive; every action is undoable for six seconds.`
        }
        action={
          <div className="flex items-center gap-2">
            <label
              htmlFor="finding-status"
              className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]"
            >
              Status
            </label>
            <select
              id="finding-status"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as DeadCodeStatus)}
              className="h-8 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2 text-xs text-[var(--color-text-secondary)]"
            >
              {STATUS_ORDER.map((s) => (
                <option key={s} value={s}>
                  {DEAD_CODE_STATUS_LABELS[s]}
                </option>
              ))}
            </select>
          </div>
        }
      >
        {/* A failure with nothing to show has to be the whole story: a table or
            an empty state underneath it would restate the failure as "clean". */}
        {tableError && tableFindings.length === 0 && !tableLoading ? (
          <RetryCard
            title="Couldn't load findings"
            error={tableError}
            onRetry={retryTable}
          />
        ) : tableLoading && tableFindings.length === 0 ? (
          <Skeleton className="h-40 w-full rounded-lg" />
        ) : /* A clean repository gets said out loud. Keyed off the fetched
            payload, not the locally filtered list: resolving the last row must
            not swap the table out, because undoing it would then bring the row
            back into a section that had remounted. Only for the open slice:
            swapping the table out while reviewing acknowledged findings would
            strand the user there. */
        statusFilter === "open" && fetched.length === 0 && findingsList.length === 0 ? (
          <EmptyState
            icon={<Trash2 className="h-6 w-6" />}
            title="No dead code found"
            description="Nothing in this repository is currently flagged as unreachable, unused or zombie. Re-run the analysis after a large refactor, when whole call paths tend to go quiet at once."
          />
        ) : (
          <>
            {/* A failed refresh over data we already hold: say so, but keep the
                rows. Replacing a working table with an error card loses the
                user's place over a transient blip. */}
            {tableError && (
              <RetryCard
                title="Couldn't refresh findings"
                error={tableError}
                onRetry={retryTable}
              />
            )}
            <FindingsTable
              findings={tableFindings}
              onPatch={handlePatch}
              onBulkResolve={handleBulkResolve}
              onGeneratePrompt={handlePropose}
              fileHref={(p) => adapter.fileHref(p)}
              onNavigate={(href) => adapter.navigate(href)}
              {...(adapter.graphHref ? { graphHref: (p: string) => adapter.graphHref!(p) } : {})}
              status={statusFilter}
              isLoading={tableLoading}
            />
          </>
        )}
      </OverviewSection>

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
