/**
 * The decisions page as the web composes it: the lede, the agent handoff, the
 * digest over the review lanes, and the checkout facts. Each piece is the
 * shared component the web page mounts; this file is only the host half the
 * web keeps in `components/decisions/*` (fetching through the extension host
 * instead of `/api`, and an epoch bump instead of SWR revalidation).
 */

import * as React from "react";
import { toast } from "sonner";
import { PageLede } from "@repowise-dev/ui/shared/page-lede";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { AiPromptButton } from "@repowise-dev/ui/health/ai-prompt-button";
import { CheckoutFactsDisclosure, reviewEach } from "@repowise-dev/ui/decisions";
import { DecisionDigest } from "@repowise-dev/ui/decisions/decision-digest";
import { DecisionCreateForm } from "@repowise-dev/ui/decisions/decision-create-form";
import {
  DecisionAgentHero,
  DecisionAgentPrompts,
} from "@repowise-dev/ui/decisions/decision-agent-prompts";
import {
  DecisionRecordActions,
  DecisionReviewLanes,
  recordPromptKinds,
  type DecisionRecordPromptKind,
  type DecisionSection,
} from "@repowise-dev/ui/decisions/decision-review-lanes";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@repowise-dev/ui/ui/dialog";
import { formatNumber, stripMarkdown } from "@repowise-dev/ui/lib/format";
import type {
  DecisionAgentPromptKind,
  DecisionLaneCounts,
  DecisionRecord,
} from "@repowise-dev/types/decisions";
import type { WebviewHost } from "../../runtime/rpc";
import { ADD, AGENT, BOARD, HERO, REVIEW, VIEW } from "./copy";
import { useRead } from "./useRead";

/** Rows the table holds at once. The server pages; this is one window. */
const PAGE_SIZE = 50;
/** Rows the digest's This week shows before pointing at the Recent section. */
const RECENT_DIGEST_ROWS = 5;

const FOOT_LINK =
  "rounded text-xs text-[var(--color-accent-primary)] underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]";

/** What every decisions read on the board shares: the host and the epoch a write bumps. */
export interface BoardContext {
  host: WebviewHost;
  repoId: string;
  /** Changes after every write and every index refresh; every read keys on it. */
  epoch: string;
  /** Refetch every decisions read on the board. */
  refresh: () => void;
  LinkComponent: React.ElementType<{ href: string; className?: string; children: React.ReactNode }>;
}

export function decisionHref(repoId: string, decisionId: string): string {
  return `/repos/${repoId}/decisions/${decisionId}`;
}

/** The page: lede, handoff, board and checkout facts, in the web's order. */
export function DecisionsPage({ ctx }: { ctx: BoardContext }) {
  const { host, epoch } = ctx;
  const lanes = useRead("lanes", () => host.api.decisionLaneCounts(), epoch);
  // The status-column projection, only the fallback for a server without
  // `/lane-counts`; the web page seeds its first paint from it the same way.
  const counts = useRead(lanes.error ? "counts" : null, () => host.api.decisionCounts(), epoch);
  const facts = useRead("facts", () => host.api.checkoutFacts(), epoch);

  const total = lanes.data?.total ?? counts.data?.total;
  const settled = lanes.data !== undefined || counts.data !== undefined || counts.error !== undefined;

  return (
    <PageShell title={VIEW.title} description={VIEW.description}>
      {settled && (
        <DecisionsLede
          lanes={lanes.data}
          fallback={{
            total: counts.data?.total,
            proposed: counts.data?.proposed ?? 0,
            active: counts.data?.active ?? 0,
          }}
        />
      )}

      <DecisionAgentPanel ctx={ctx} />

      <DecisionsBoard ctx={ctx} laneCounts={lanes.data} hasRecords={total !== 0} />

      {facts.data && (
        <CheckoutFactsDisclosure
          title={VIEW.checkoutTitle}
          description={VIEW.checkoutDescription}
          facts={facts.data.episodes}
          available={facts.data.available}
          total={facts.data.total}
        />
      )}
    </PageShell>
  );
}

/**
 * The headline figure: what is in force, counted from acceptance rows. The
 * same reasoning as the web lede; see `components/decisions/decisions-lede.tsx`.
 */
function DecisionsLede({
  lanes,
  fallback,
}: {
  lanes: DecisionLaneCounts | undefined;
  fallback: { total: number | undefined; proposed: number; active: number };
}) {
  const total = lanes?.total ?? fallback.total;
  const proposed = lanes?.candidates ?? fallback.proposed;
  const measuredByLane = lanes !== undefined;
  const accepted = measuredByLane
    ? lanes.active + lanes.needs_review + lanes.uncheckable
    : fallback.active;
  const inForce = lanes?.in_force ?? accepted;
  const empty = total === 0;

  return (
    <PageLede
      label={measuredByLane ? VIEW.ledeAccepted : VIEW.ledeConfirmed}
      value={formatNumber(inForce)}
      {...(total !== undefined ? { unit: `of ${formatNumber(total)} recorded` } : {})}
      layout="beside"
    >
      {empty ? (
        <p>
          {VIEW.ledeEmptyBefore}
          <code>{VIEW.ledeEmptyCode}</code>
          {VIEW.ledeEmptyAfter}
        </p>
      ) : inForce === 0 ? (
        <>
          <p>{VIEW.ledeNoGovernance}</p>
          <p>{VIEW.ledeCandidates(proposed)}</p>
        </>
      ) : (
        <>
          <p>{VIEW.ledeAcceptedRecords(inForce)}</p>
          {proposed > 0 && <p>{VIEW.ledeAcceptedMore(proposed)}</p>}
        </>
      )}
    </PageLede>
  );
}

/** One server-rendered decision prompt; a failed read shows no button. */
function usePrompt(ctx: BoardContext, kind: DecisionAgentPromptKind | null, decisionId?: string) {
  return useRead(
    kind ? `prompt:${kind}:${decisionId ?? ""}` : null,
    () => ctx.host.api.decisionAgentPrompt(kind!, decisionId),
    ctx.epoch,
  );
}

/**
 * The handoff: the discovery prompt behind one copy button, the other two
 * prompts beside it, and the manual path in a dialog.
 */
function DecisionAgentPanel({ ctx }: { ctx: BoardContext }) {
  const [open, setOpen] = React.useState(false);
  const discover = usePrompt(ctx, "discover");
  const agreements = usePrompt(ctx, "agreements").data;
  const add = usePrompt(ctx, "add").data;
  const reviewAll = usePrompt(ctx, "review_all").data;

  const prompt = discover.data;
  const checking = prompt?.kind === "discover";
  const heading = prompt ? (checking ? HERO.discoverHeading(prompt.count) : HERO.addHeading) : undefined;
  const description = prompt ? (checking ? HERO.discoverDescription : HERO.addDescription) : undefined;

  return (
    <>
      <DecisionAgentHero
        prompt={discover.error ? null : prompt}
        {...(heading ? { heading } : {})}
        {...(description ? { description } : {})}
        copyLabel={HERO.copy}
        copiedLabel={HERO.copied}
        failedLabel={HERO.copyFailed}
        reviewAll={reviewAll?.text ? { text: reviewAll.text, label: HERO.reviewAll } : null}
        footnote={
          <span>
            {HERO.footnoteBefore}
            <code>{HERO.footnoteCode}</code>
            {HERO.footnoteAfter}
          </span>
        }
        links={
          <>
            {checking && add?.text && (
              <AiPromptButton variant="ghost" copyText={() => add.text} label={HERO.recordMissed} />
            )}
            {agreements?.text && (
              <AiPromptButton variant="ghost" copyText={() => agreements.text} label={AGENT.agreements} />
            )}
            <button type="button" className={FOOT_LINK} onClick={() => setOpen(true)}>
              {HERO.addByHand}
            </button>
          </>
        }
      />
      {discover.error !== undefined && (
        <div className="flex justify-end">
          <button type="button" className={FOOT_LINK} onClick={() => setOpen(true)}>
            {ADD.trigger}
          </button>
        </div>
      )}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{ADD.title}</DialogTitle>
            <DialogDescription>{ADD.description}</DialogDescription>
          </DialogHeader>
          <DecisionCreateForm
            onSubmit={async (input) => {
              await ctx.host.api.createDecision(input);
            }}
            onCreated={() => {
              setOpen(false);
              ctx.refresh();
            }}
            onCancel={() => setOpen(false)}
          />
        </DialogContent>
      </Dialog>
    </>
  );
}

/** One record's agent prompts, each its own read, for the record's drawer. */
export function DecisionRecordPrompts({
  ctx,
  decision: d,
}: {
  ctx: BoardContext;
  decision: Pick<DecisionRecord, "id" | "currency" | "pinned" | "status">;
}) {
  return (
    <>
      {recordPromptKinds(d).map((kind) => (
        <RecordPrompt key={kind} ctx={ctx} decisionId={d.id} kind={kind} />
      ))}
    </>
  );
}

function RecordPrompt({
  ctx,
  decisionId,
  kind,
}: {
  ctx: BoardContext;
  decisionId: string;
  kind: DecisionRecordPromptKind;
}) {
  const prompt = usePrompt(ctx, kind, decisionId).data;
  return <DecisionAgentPrompts prompts={[prompt ? { text: prompt.text, label: AGENT[kind] } : null]} />;
}

/** Section badges from the lane counts. A count the server did not send stays unknown. */
function sectionCounts(counts: DecisionLaneCounts): Partial<Record<DecisionSection, number>> {
  const out: Partial<Record<DecisionSection, number>> = {
    candidates: counts.candidates,
    history: counts.history,
  };
  if (counts.in_force != null) out.in_force = counts.in_force;
  if (counts.recent != null) out.recent = counts.recent;
  return out;
}

/** The digest and the full record under one piece of state, as on the web. */
function DecisionsBoard({
  ctx,
  laneCounts: counts,
  hasRecords,
}: {
  ctx: BoardContext;
  laneCounts: DecisionLaneCounts | undefined;
  hasRecords: boolean;
}) {
  const { host, epoch, repoId, LinkComponent } = ctx;
  const [lane, setLane] = React.useState<DecisionSection>("in_force");
  const [movedOnly, setMovedOnly] = React.useState(false);
  const listRef = React.useRef<HTMLDivElement>(null);
  const add = usePrompt(ctx, "add").data;
  const health = useRead(hasRecords ? "health" : null, () => host.api.decisionHealth(), epoch);
  const recent = useRead(
    hasRecords ? "recent" : null,
    () => host.api.listDecisions({ lane: "recent", include_proposed: true, limit: RECENT_DIGEST_ROWS }),
    epoch,
  );
  const writes = useReviewWrites(ctx);
  const loadRecordPrompt = React.useCallback(
    (d: DecisionRecord, kind: DecisionRecordPromptKind) =>
      host.api.decisionAgentPrompt(kind, d.id).then((r) => r.text),
    [host],
  );

  const showList = (next: DecisionSection, moved: boolean) => {
    setLane(next);
    setMovedOnly(moved);
    listRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  if (!hasRecords) return null;

  return (
    <>
      <DecisionDigest
        needsReview={counts?.needs_review}
        onReviewNeedsReview={() => showList("in_force", true)}
        ungovernedHotspots={health.data?.ungoverned_hotspots}
        ungovernedAction={
          add?.text ? (
            <AiPromptButton variant="ghost" copyText={() => add.text} label={HERO.recordMissed} />
          ) : undefined
        }
        recent={recent.data}
        recentTotal={counts?.recent}
        onShowAllRecent={() => showList("recent", false)}
        recentHref={(d) => decisionHref(repoId, d.id)}
        LinkComponent={LinkComponent}
        renderRecentActions={(d) => (
          <DecisionRecordActions
            decision={d}
            section="recent"
            onPin={(rec, pinned) => void writes.review(rec, pinned ? "pin" : "unpin")}
            onReaffirm={(rec) => void writes.review(rec, "reaffirm")}
            loadRecordPrompt={loadRecordPrompt}
            pending={writes.pending.has(d.id)}
            href={decisionHref(repoId, d.id)}
            LinkComponent={LinkComponent}
          />
        )}
      />
      <div ref={listRef} className="scroll-mt-6">
        <OverviewSection title={VIEW.reviewTitle} description={VIEW.reviewDescription}>
          <DecisionReviewWrapper
            ctx={ctx}
            lane={lane}
            onLaneChange={(next) => {
              setLane(next);
              setMovedOnly(false);
            }}
            movedOnly={movedOnly && lane === "in_force"}
            onClearMoved={() => setMovedOnly(false)}
            counts={counts}
            writes={writes}
            loadRecordPrompt={loadRecordPrompt}
          />
        </OverviewSection>
      </div>
    </>
  );
}

/**
 * The lanes with their per-lane fetch, paging and source filter. The lane is
 * a server query parameter, not a filter over a loaded window.
 */
function DecisionReviewWrapper({
  ctx,
  lane,
  onLaneChange,
  movedOnly,
  onClearMoved,
  counts,
  writes,
  loadRecordPrompt,
}: {
  ctx: BoardContext;
  lane: DecisionSection;
  onLaneChange: (lane: DecisionSection) => void;
  movedOnly: boolean;
  onClearMoved: () => void;
  counts: DecisionLaneCounts | undefined;
  writes: ReturnType<typeof useReviewWrites>;
  loadRecordPrompt: (d: DecisionRecord, kind: DecisionRecordPromptKind) => Promise<string>;
}) {
  const { host, epoch, repoId, LinkComponent } = ctx;
  const [page, setPage] = React.useState(0);
  const [source, setSource] = React.useState("all");
  // The last page known to hold rows, once a step past it came back empty.
  // Without a measured total (under a source filter) a full window is the only
  // hint of more, so a filter matching exactly one window would otherwise
  // offer Next forever.
  const [lastPage, setLastPage] = React.useState<number | null>(null);

  const background = lane === "candidates";
  const serverLane = movedOnly ? "needs_review" : lane;
  // A new lane starts at its first page. Reset while rendering, so the old
  // offset is never fetched for the new lane.
  const [pagedLane, setPagedLane] = React.useState(serverLane);
  if (pagedLane !== serverLane) {
    setPagedLane(serverLane);
    setPage(0);
    setLastPage(null);
  }
  const offset = pagedLane === serverLane ? page * PAGE_SIZE : 0;

  const rowsRead = useRead(
    `lane:${serverLane}:${source}:${offset}`,
    () =>
      host.api.listDecisions({
        lane: serverLane,
        ...(source !== "all" ? { source } : {}),
        include_proposed: true,
        limit: PAGE_SIZE,
        offset,
        ...(background ? { sort: "review" as const } : {}),
      }),
    epoch,
  );
  const data = rowsRead.data;
  const rows = data ?? [];

  const changeSource = React.useCallback((next: string) => {
    setSource(next);
    setPage(0);
    setLastPage(null);
  }, []);

  // Reviewing the last row of a later page leaves that page empty: step back.
  React.useEffect(() => {
    if (page > 0 && data !== undefined && data.length === 0) {
      setLastPage(page - 1);
      setPage((p) => Math.max(0, p - 1));
    }
  }, [page, data]);

  const badges = counts ? sectionCounts(counts) : undefined;
  const laneTotal = movedOnly ? counts?.needs_review : badges?.[lane];
  const total = source === "all" ? laneTotal : undefined;
  const first = page * PAGE_SIZE + 1;
  const last = page * PAGE_SIZE + rows.length;
  const hasNext =
    total !== undefined
      ? last < total
      : rows.length === PAGE_SIZE && (lastPage === null || page < lastPage);
  const { pending, review, dismissMany } = writes;

  return (
    <div className="space-y-4">
      <DecisionReviewLanes
        lane={lane}
        onLaneChange={onLaneChange}
        {...(background ? { order: "review" as const } : {})}
        decisions={data}
        {...(badges ? { counts: badges } : {})}
        source={source}
        onSourceChange={changeSource}
        onAccept={(d, options) => void review(d, "accept", options?.reason)}
        onDismiss={(d) => void review(d, "dismiss")}
        onReaffirm={(d) => void review(d, "reaffirm")}
        onPin={(d, pinned) => void review(d, pinned ? "pin" : "unpin")}
        recordPrompts={(d) => <DecisionRecordPrompts ctx={ctx} decision={d} />}
        loadRecordPrompt={loadRecordPrompt}
        onDismissMany={dismissMany}
        {...(movedOnly
          ? {
              filterNote: (
                <p className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span>{BOARD.movedOnly(counts?.needs_review ?? rows.length)}</span>
                  <button type="button" onClick={onClearMoved} className={FOOT_LINK}>
                    {BOARD.showAll}
                  </button>
                </p>
              ),
            }
          : {})}
        pendingIds={pending}
        repoId={repoId}
        LinkComponent={LinkComponent}
        error={rowsRead.error}
        isLoading={rowsRead.isLoading}
        onRetry={rowsRead.reload}
      />

      {(page > 0 || hasNext) && (
        <div className="flex items-center justify-between gap-4 border-t border-[var(--color-border-default)] pt-3">
          <p className="font-mono text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
            {rows.length > 0 ? `${first}–${last}` : "0"}
            {total !== undefined ? ` of ${formatNumber(total)}` : ""}
          </p>
          <div className="flex items-center gap-2">
            <PageButton
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0 || rowsRead.isLoading}
            >
              {REVIEW.previous}
            </PageButton>
            <PageButton onClick={() => setPage((p) => p + 1)} disabled={!hasNext || rowsRead.isLoading}>
              {REVIEW.next}
            </PageButton>
          </div>
        </div>
      )}
    </div>
  );
}

type ReviewVerb = "accept" | "dismiss" | "reaffirm" | "pin" | "unpin";

const DONE: Record<ReviewVerb, string> = {
  accept: REVIEW.accepted,
  dismiss: REVIEW.dismissed,
  reaffirm: REVIEW.reaffirmed,
  pin: REVIEW.pinned,
  unpin: REVIEW.unpinned,
};
const FAILED: Record<ReviewVerb, (message: string) => string> = {
  accept: REVIEW.acceptFailed,
  dismiss: REVIEW.dismissFailed,
  reaffirm: REVIEW.reaffirmFailed,
  pin: REVIEW.pinFailed,
  unpin: REVIEW.unpinFailed,
};

/** The review writes and their in-flight set, as the web board runs them. */
function useReviewWrites(ctx: BoardContext) {
  const { host, refresh } = ctx;
  const [pending, setPending] = React.useState<ReadonlySet<string>>(new Set());
  const review = React.useCallback(
    async (d: DecisionRecord, verb: ReviewVerb, reason?: string) => {
      setPending((prev) => new Set(prev).add(d.id));
      try {
        if (verb === "reaffirm") {
          await host.api.reaffirmDecision(d.id);
        } else if (verb === "pin" || verb === "unpin") {
          await host.api.setDecisionPinned(d.id, verb === "pin");
        } else {
          await host.api.patchDecision(d.id, {
            status: verb === "accept" ? "active" : "dismissed",
            ...(verb === "accept" && reason ? { reason } : {}),
          });
        }
        toast.success(DONE[verb]);
        refresh();
      } catch (err) {
        // The acceptance contract names what a refused record lacks; that
        // sentence is the instruction, so it is surfaced under the title.
        const message = err instanceof Error ? err.message : null;
        toast.error(stripMarkdown(d.title), {
          description: message === null ? REVIEW.actionFailed : FAILED[verb](message),
        });
      } finally {
        setPending((prev) => {
          const next = new Set(prev);
          next.delete(d.id);
          return next;
        });
      }
    },
    [host, refresh],
  );

  const dismissMany = React.useCallback(
    async (decisions: DecisionRecord[], onProgress: (settled: number) => void) => {
      const result = await reviewEach(
        decisions,
        (d) => host.api.patchDecision(d.id, { status: "dismissed" }),
        onProgress,
      );
      if (result.done.length > 0) toast.success(REVIEW.bulkDismissed(result.done.length));
      refresh();
      return result;
    },
    [host, refresh],
  );
  return { pending, review, dismissMany };
}

function PageButton({
  onClick,
  disabled,
  children,
}: {
  onClick: () => void;
  disabled: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="rounded-md border border-[var(--color-border-default)] px-2.5 py-1 text-xs text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-border-hover)] hover:text-[var(--color-text-primary)] disabled:opacity-40 disabled:hover:border-[var(--color-border-default)] disabled:hover:text-[var(--color-text-secondary)]"
    >
      {children}
    </button>
  );
}
