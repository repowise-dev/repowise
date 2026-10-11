"use client";

/**
 * Dead Code view, on the section design language.
 *
 * Three things, top to bottom: a lede with the reclaimable line count and one
 * posture sentence, the safe-to-delete list, and the full findings table with
 * its status control in the section header. Carries the optimistic row patch
 * + Undo toast, bulk resolve, the "Propose cleanup" agent brief, and
 * Re-analyze.
 *
 * Owner and confidence-by-kind rollups used to sit between the list and the
 * table. They cut the same findings two more ways without changing what to do
 * next, so the page no longer carries them; the table sorts by owner and
 * filters by kind and confidence when that question comes up.
 *
 * Presentation + orchestration only: the host injects data fetching,
 * mutations, links, and navigation through a {@link DeadCodeAdapter}, so web
 * and hosted render the same view from one source.
 */

import { useId, useMemo, useState } from "react";
import useSWR from "swr";
import { toast } from "sonner";
import type {
  DeadCodeFinding,
  DeadCodeStatus,
  DeadCodeSummary,
} from "@repowise-dev/types/dead-code";

import { Button } from "../ui/button";
import { Skeleton } from "../ui/skeleton";
import { ApiError } from "../shared/api-error";
import { EmptyState } from "../shared/empty-state";
import { Segmented } from "../shared/segmented";
import { OverviewSection } from "../overview/section";
import { AiPromptModal } from "../health/ai-prompt-modal";
import { buildDeadCodeAiPrompt } from "../health/ai-prompt-builder";

import { AnalysedAt, DeadCodeLede } from "./dead-code-lede";
import { SafeToDeletePile } from "./safe-to-delete-pile";
import { FindingsTable } from "./findings-table";
import { DEAD_CODE_STATUS_LABELS } from "./finding-cells";
import type { DeadCodeAdapter } from "./dead-code-adapter";
import { toFriendlyMessage } from "../lib/errors";
import { formatNumber } from "../lib/format";

/**
 * Server ceiling for one findings page (`limit` is clamped to 500 server-side).
 * Hitting it means the table shows a slice, and every count derived from that
 * slice has to say so rather than reading as the whole repository.
 */
const FINDINGS_LIMIT = 500;

/** Order of the status control; "open" first because it is the working list. */
const STATUS_OPTIONS = (["open", "acknowledged", "resolved", "false_positive"] as const).map(
  (value) => ({ value, label: DEAD_CODE_STATUS_LABELS[value] }),
);

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
  return (
    <ApiError title={title} message={toFriendlyMessage(error)} onRetry={onRetry} size="compact" />
  );
}

export function DeadCodeView({ adapter }: { adapter: DeadCodeAdapter }) {
  const [analyzing, setAnalyzing] = useState(false);
  const [promptIds, setPromptIds] = useState<string[] | null>(null);
  /** Which slice the findings table shows; the lede and the list stay open-only. */
  const [statusFilter, setStatusFilter] = useState<DeadCodeStatus>("open");
  // Optimistic row state lives here, not in the table: the list and the table
  // read one slice, so resolving a row (or undoing it) moves both together.
  const [overrides, setOverrides] = useState<Record<string, DeadCodeFinding>>({});
  const reasonId = useId();

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

  // Single findings fetch feeds the list AND the table (which filters this
  // slice client-side); no second fetch.
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
  // "what can I delete", so it gets its own fetch and leaves the list and the
  // summary reading the open slice.
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
    // it is open now and belongs in the list. Refetching instead would race the
    // optimistic override that is masking it.
    const reopened = Object.values(overrides).filter((f) => !inPayload.has(f.id));
    return [...fetched.map((f) => overrides[f.id] ?? f), ...reopened].filter(
      (f) => f.status === "open",
    );
  }, [fetched, overrides]);
  const safeFindings = useMemo(
    () => findingsList.filter((f) => f.safe_to_delete),
    [findingsList],
  );

  // What the findings table renders. Same override + status treatment as the
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

  // Nothing open, per the summary and the payload. Keyed off the fetched
  // payload, not the locally filtered list: resolving the last row must not
  // swap the sections out, or undo would restore it into a remounted page.
  const clean =
    statusFilter === "open" &&
    !findingsError &&
    fetched.length === 0 &&
    (summary?.total_findings ?? 0) === 0 &&
    (findings !== undefined || summary !== undefined);
  // Only a recorded run can call a zero clean; an unknown one is "not yet".
  const analyzed = Boolean(summary?.analyzed_at);

  // "Propose cleanup" opens the shared AI-prompt modal seeded with the safe
  // list, with the same agent picker as every other AI action in the dashboard.
  const handlePropose = (findingIds: string[]) => setPromptIds(findingIds);

  // Seeded from both slices: the list's CTA names open findings even while the
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
    // Guard here rather than on each button: two clicks would race a second
    // job into a 409.
    if (analyzing || adapter.analyzeDisabledReason) return;
    setAnalyzing(true);
    let jobId: string | undefined;
    try {
      const started = await adapter.analyze();
      jobId = started?.job_id;
      toast.success(
        jobId && adapter.waitForAnalysis
          ? "Analysis started. This page refreshes when it finishes."
          : "Analysis started. Results will appear shortly.",
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
    // The lede is a server-derived count of the open slice; without this it
    // drifts by one on every row action and quietly disagrees with the table.
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
    // Reflect only the rows the server confirmed, never a positional guess.
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

  // The tab's one action. The same element renders wherever the lede cannot,
  // so a failed summary still leaves a way to re-run the pass. A host that
  // cannot run one says why beside the disabled button, not after a click.
  const disabledReason = adapter.analyzeDisabledReason;
  const analyzeAction = (label: string) => (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <Button
        size="sm"
        variant="outline"
        className="h-8"
        onClick={handleAnalyze}
        disabled={analyzing || Boolean(disabledReason)}
        {...(disabledReason ? { "aria-describedby": reasonId } : {})}
      >
        {analyzing ? "Analyzing…" : label}
      </Button>
      {disabledReason && (
        <span id={reasonId} className="text-xs text-[var(--color-text-tertiary)]">
          {disabledReason}
        </span>
      )}
    </div>
  );

  const openCount = findingsList.length;
  const sectionDescription = tableLoading
    ? "Loading…"
    : statusFilter !== "open"
      ? `${reviewTruncated ? "The first " : ""}${formatNumber(tableFindings.length)} ${DEAD_CODE_STATUS_LABELS[statusFilter].toLowerCase()} finding${tableFindings.length === 1 ? "" : "s"}. Reopen one to put it back on the open list.`
      : truncated && summary
        ? `Showing the first ${formatNumber(openCount)} of ${formatNumber(summary.total_findings)} open findings. Every action can be undone for six seconds.`
        : "Resolve, acknowledge or mark a false positive. Every action can be undone for six seconds.";

  return (
    <div className="flex flex-col gap-6 sm:gap-8">
      {loadingSummary ? (
        // Shapes and widths match the lede, so nothing reflows when it lands.
        <div className="flex flex-col gap-5 lg:flex-row lg:gap-12">
          <Skeleton className="h-[72px] w-full rounded-lg lg:w-[220px]" />
          <Skeleton className="h-[72px] w-full max-w-[62ch] rounded-lg" />
        </div>
      ) : summary ? (
        clean && !analyzed ? (
          // A zero with no recorded run is "not yet", never an all-clear.
          <EmptyState
            title="Not analysed yet"
            description="Dead-code analysis has not run for this repository. Run it to find unreachable files, unused exports and zombie packages."
          >
            {analyzeAction("Run analysis")}
          </EmptyState>
        ) : (
          <DeadCodeLede summary={summary} action={analyzeAction("Re-analyze")}>
            {clean ? (
              <EmptyState
                tone="positive"
                className="items-start px-0 py-0 text-left"
                title="No dead code found"
                description={
                  <>
                    No unreachable files, unused exports or zombie packages across this
                    repository.
                    <AnalysedAt at={summary.analyzed_at} />
                  </>
                }
                // Resolved and set-aside findings stay reviewable from here,
                // since the clean page carries no table to switch.
                secondaryAction={{
                  label: "Review past findings",
                  onClick: () => setStatusFilter("resolved"),
                }}
              />
            ) : undefined}
          </DeadCodeLede>
        )
      ) : summaryError ? (
        <div className="flex flex-col gap-3">
          <RetryCard
            title="Couldn't load summary"
            error={summaryError}
            onRetry={() => void mutateSummary()}
          />
          {analyzeAction("Re-analyze")}
        </div>
      ) : null}

      {!clean && findings && safeFindings.length > 0 && (
        <SafeToDeletePile
          findings={safeFindings}
          onPropose={handlePropose}
          onSelect={(f) => adapter.navigate(adapter.fileHref(f.file_path))}
        />
      )}

      {/* The status control lives in this header rather than among the table's
          own filters because the table swaps out for empty and error states,
          and a control living inside it would go with them. */}
      {!clean && (
        <OverviewSection
          title="All findings"
          description={sectionDescription}
          action={
            <Segmented<DeadCodeStatus>
              label="Status"
              value={statusFilter}
              onChange={setStatusFilter}
              options={STATUS_OPTIONS}
            />
          }
        >
          {/* A failure with nothing to show has to be the whole story: a table
              or an empty state underneath it would restate the failure as
              "clean". */}
          {tableError && tableFindings.length === 0 && !tableLoading ? (
            <RetryCard
              title="Couldn't load findings"
              error={tableError}
              onRetry={retryTable}
            />
          ) : tableLoading && tableFindings.length === 0 ? (
            <Skeleton className="h-40 w-full rounded-lg" />
          ) : statusFilter === "open" && openCount === 0 && fetched.length > 0 && !truncated ? (
            // Every row in the payload was actioned on this page. The section
            // stays mounted so Undo can put a row straight back.
            <EmptyState
              tone="positive"
              title="Every finding is resolved or set aside"
              description="Switch the status above to review or reopen one."
            />
          ) : (
            <>
              {/* A failed refresh over data we already hold: say so, but keep
                  the rows. Replacing a working table with an error card loses
                  the user's place over a transient blip. */}
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
      )}

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
        description="A ready-to-paste prompt that has your AI agent verify and remove these findings safely, in reviewable commits."
      />
    </div>
  );
}
