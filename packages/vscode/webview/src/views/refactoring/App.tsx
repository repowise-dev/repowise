/**
 * Refactoring panel: the web Refactoring page. The same board (lede, Start
 * here, the rows with their filters and paging), the same opportunity drawer
 * and plan inspector, and the same agent prompt dialog, over the same
 * endpoints through the host.
 *
 * Mirrors `packages/web/src/app/repos/[id]/refactoring/page.tsx`. What differs
 * is only what an editor needs: the URL params become state (a CodeLens or a
 * tree item opens a plan by id, a file's own entry seeds the search with its
 * path), and a file reference opens in an editor column. Model code drafting
 * is a local-server setting the web page owns; the panel leaves it out.
 */

import { useCallback, useDeferredValue, useEffect, useMemo, useState } from "react";
import { RotateCw, Wrench } from "lucide-react";
import { ApiError } from "@repowise-dev/ui/shared/api-error";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { ViewTabs } from "@repowise-dev/ui/shared/view-tabs";
import { Skeleton, SkeletonRegion } from "@repowise-dev/ui/ui/skeleton";
import {
  OpportunityDrawer,
  RefactoringBoard,
  RefactoringDrawer,
  STRUCTURAL_TYPES,
  TYPE_ORDER,
  typeMeta,
  type RefactoringBoardServerState,
} from "@repowise-dev/ui/refactoring";
import { AiPromptModal, type PromptSource } from "@repowise-dev/ui/health";
import type {
  Confidence,
  EffortBucket,
  OpportunityStatus,
  RefactoringOpportunity,
  RefactoringOrder,
  RefactoringScope,
} from "@repowise-dev/types/refactoring";
import type { ViewProps } from "../../runtime/mount";
import type { WebviewHost } from "../../runtime/rpc";
import { fileHref, useFileLinks } from "../../runtime/fileLinks";
import { useHostData } from "./useHostData";

/** The web page's English strings (`views.refactoring`, `common`). */
const COPY = {
  title: "Refactoring",
  description:
    "One opportunity per file: the ordered steps the health pass wrote from your code. Open one to see the change, or hand it to a coding agent.",
  promptTitle: "AI refactoring prompt",
  promptDescription:
    "A ready-to-paste plan that hands your AI coding agent the ordered steps, which of them are mechanical, the evidence behind the diagnosis, and the id to query it back.",
  loading: "Loading refactoring opportunities",
  loadFailed:
    "Couldn't load refactoring opportunities. The repo may not be indexed yet, or the API is unreachable.",
  loadFailedTitle: "Couldn't load refactoring",
  unavailableTitle: "Refactoring analysis has not run yet",
  unavailableDescription:
    "Run repowise update in this repository to analyse it. Opportunities appear here once the health pass has finished.",
  tabAll: "All",
  tabStructural: "Structural",
  sectionTitleAll: "All opportunities",
  sectionTitleFixFirst: "Opportunities worth doing",
  sectionTitleStructural: "Structural opportunities",
  refresh: "Refresh",
  retry: "Retry",
} as const;

const PANEL_ID = "refactoring-tab-panel";
const PAGE_SIZE = 60;
const STRUCTURAL_CSV = (STRUCTURAL_TYPES as readonly string[]).join(",");
type TypeFilter = "all" | "structural" | (typeof TYPE_ORDER)[number];

/** The list's type tab, as the one lead type or the structural set. */
function leadTypeFor(type: TypeFilter): string | undefined {
  if (type === "all") return undefined;
  if (type === "structural") return STRUCTURAL_CSV;
  return type;
}

/** The board's server-side filters, as the web page keeps them. */
interface Filters {
  query: string;
  order: RefactoringOrder;
  status: OpportunityStatus;
  effort: EffortBucket | null;
  confidence: Confidence | null;
  mechanicalOnly: boolean;
  scope: RefactoringScope;
  offset: number;
}

function initialFilters(filePath: string | undefined): Filters {
  return {
    // A file's own entry (its CodeLens, its tree row) lists that file's work:
    // its path in the search, over the whole inventory rather than Fix first.
    query: filePath ?? "",
    order: "queue",
    status: "open",
    effort: null,
    confidence: null,
    mechanicalOnly: false,
    // What Fix first would take, by default; the full inventory on request.
    scope: filePath ? "all" : "fix_first",
    offset: 0,
  };
}

export function App({ host, params, refreshToken }: ViewProps<"refactoring">) {
  useFileLinks(host);
  const [type, setType] = useState<TypeFilter>("all");
  const [filters, setFilters] = useState<Filters>(() => initialFilters(params.filePath));
  const deferredQuery = useDeferredValue(filters.query);
  const [openId, setOpenId] = useState<string | null>(null);
  const [openPlanId, setOpenPlanId] = useState<string | null>(params.planId ?? null);
  // The host can push new params into an open panel; honor them.
  useEffect(() => setOpenPlanId(params.planId ?? null), [params.planId]);

  const listKey = JSON.stringify([type, { ...filters, query: deferredQuery }, refreshToken]);
  const list = useHostData(
    () =>
      host.api.refactoringOpportunities({
        refactoringType: leadTypeFor(type),
        status: filters.status,
        scope: filters.scope,
        search: deferredQuery || undefined,
        effort: filters.effort ?? undefined,
        confidence: filters.confidence ?? undefined,
        mechanical: filters.mechanicalOnly,
        order: filters.order,
        // The row renders step counts, not steps.
        stepPreview: 0,
        limit: PAGE_SIZE,
        offset: filters.offset,
      }),
    listKey,
  );
  // The structural head for Start here: the whole repository in the list's
  // scope, not a slice of whatever filter the reader chose.
  const structural = useHostData(
    () =>
      type === "all"
        ? host.api.refactoringOpportunities({
            refactoringType: STRUCTURAL_CSV,
            scope: filters.scope,
            order: "rank",
            stepPreview: 0,
            limit: 100,
          })
        : Promise.resolve(null),
    JSON.stringify([type, filters.scope, refreshToken]),
  );
  const detail = useHostData(
    () => (openId ? host.api.refactoringOpportunity(openId) : Promise.resolve(null)),
    `${openId}:${refreshToken}`,
    false,
  );
  const plan = useHostData(
    () => (openPlanId ? host.api.refactoringPlan(openPlanId) : Promise.resolve(null)),
    `${openPlanId}:${refreshToken}`,
    false,
  );
  const prompt = usePromptDialog(host);
  const { reload: reloadBoard } = list;
  const { reload: reloadStructural } = structural;
  const { reload: reloadDetail } = detail;
  // Start here is its own read; a triage or a refresh moves it too.
  const reloadList = useCallback(() => {
    reloadBoard();
    reloadStructural();
  }, [reloadBoard, reloadStructural]);

  const onStatusChange = useCallback(
    async (opportunity: { opportunity_id: string }, status: OpportunityStatus) => {
      await host.api.setRefactoringOpportunityStatus(opportunity.opportunity_id, status);
      // The server decides what the rollup became; read it back.
      reloadList();
      reloadDetail();
    },
    [host, reloadList, reloadDetail],
  );
  const readSource = useCallback((path: string) => host.api.fileContent(path), [host]);

  const data = list.data;
  const facetCounts = data?.facets?.lead_type ?? {};
  const tabs = useMemo(() => buildTabs(facetCounts), [facetCounts]);
  const serverState: RefactoringBoardServerState = {
    ...filters,
    appliedScope: data?.scope ?? "all",
    hidden: data?.hidden ?? null,
    counts: data?.counts ?? null,
    total: data?.total ?? 0,
    nextOffset: data?.next_offset ?? null,
  };
  const changeType = (next: TypeFilter) => {
    setFilters((f) => ({ ...f, offset: 0 }));
    setType(next);
  };

  return (
    <PageShell
      title={COPY.title}
      icon={<Wrench />}
      description={COPY.description}
      actions={
        <button
          type="button"
          onClick={reloadList}
          className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-2.5 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:text-[var(--color-text-primary)]"
        >
          <RotateCw className="h-3.5 w-3.5" />
          {COPY.refresh}
        </button>
      }
    >
      <div className="space-y-6">
        <ViewTabs
          tabs={tabs}
          value={type}
          panelId={PANEL_ID}
          onValueChange={(id) => changeType(id as TypeFilter)}
        />
        <div id={PANEL_ID} role="tabpanel" aria-labelledby={`${PANEL_ID}-tab-${type}`} tabIndex={0}>
          {/* A failed read replaces the rows: rows left from the last query
              would sit under controls describing the new one. */}
          {list.error ? (
            <ApiError
              title={COPY.loadFailedTitle}
              message={COPY.loadFailed}
              retryLabel={COPY.retry}
              onRetry={reloadList}
            />
          ) : data?.summary?.status === "unavailable" ? (
            <EmptyState title={COPY.unavailableTitle} description={COPY.unavailableDescription} />
          ) : !data ? (
            <SkeletonRegion className="space-y-8" label={COPY.loading}>
              <Skeleton className="h-32 rounded-xl" />
              <Skeleton className="h-16 rounded-xl" />
              <Skeleton className="h-72 rounded-xl" />
            </SkeletonRegion>
          ) : (
            <RefactoringBoard
              opportunities={data.items}
              summary={data.summary ?? null}
              facets={data.facets}
              structuralOpportunities={structural.data?.items}
              serverState={serverState}
              onServerStateChange={(change) => setFilters((f) => ({ ...f, ...change }))}
              onOpen={(o) => setOpenId(o.opportunity_id)}
              onAiPrompt={prompt.forOpportunity}
              onStatusChange={onStatusChange}
              onSeeStructural={() => changeType("structural")}
              fileHref={fileHref}
              // The lede and Start here describe the whole repository, so they
              // only belong on the unfiltered view.
              showLede={type === "all"}
              sectionTitle={sectionTitle(type, serverState.appliedScope)}
            />
          )}
        </div>
      </div>

      <OpportunityDrawer
        detail={detail.data ?? null}
        open={openId !== null}
        loading={detail.loading}
        onOpenChange={(open) => {
          if (!open) setOpenId(null);
        }}
        onAiPrompt={prompt.forOpportunity}
        onStatusChange={onStatusChange}
        onOpenStep={setOpenPlanId}
        fileHref={fileHref}
        readSource={readSource}
      />

      <RefactoringDrawer
        plan={plan.data ?? null}
        open={openPlanId !== null}
        loading={plan.loading}
        onOpenChange={(open) => {
          if (!open) setOpenPlanId(null);
        }}
        onAiPrompt={prompt.forPlan}
        fileHref={fileHref}
      />

      <AiPromptModal
        open={prompt.target !== null}
        onOpenChange={(open) => {
          if (!open) prompt.close();
        }}
        getPrompt={null}
        promptSource={prompt.source}
        filePath={prompt.target?.filePath ?? null}
        title={COPY.promptTitle}
        description={COPY.promptDescription}
      />
    </PageShell>
  );
}

function buildTabs(facetCounts: Record<string, number>) {
  // Summed from the facets, which follow the status filter, as on the web.
  const total = Object.values(facetCounts).reduce((n, c) => n + c, 0);
  const structuralCount = (STRUCTURAL_TYPES as readonly string[]).reduce(
    (n, t) => n + (facetCounts[t] ?? 0),
    0,
  );
  return [
    { id: "all", label: COPY.tabAll, badge: total },
    { id: "structural", label: COPY.tabStructural, badge: structuralCount },
    ...TYPE_ORDER.filter((t) => t !== "performance_fix").map((t) => ({
      id: t,
      label: typeMeta(t).label,
      badge: facetCounts[t] ?? 0,
    })),
  ].filter((tab) => tab.id === "all" || tab.badge > 0);
}

function sectionTitle(type: TypeFilter, appliedScope: RefactoringScope | undefined): string {
  if (type === "all") {
    return appliedScope === "fix_first" ? COPY.sectionTitleFixFirst : COPY.sectionTitleAll;
  }
  if (type === "structural") return COPY.sectionTitleStructural;
  return `${typeMeta(type).label} opportunities`;
}

/**
 * The prompt dialog's subject. Core renders both prompts from the stored
 * plans, so a row hands over its opportunity (or a step its plan) by id.
 */
function usePromptDialog(host: WebviewHost) {
  const [target, setTarget] = useState<{
    kind: "opportunity" | "plan";
    id: string;
    filePath: string;
  } | null>(null);
  const source = useMemo<PromptSource | undefined>(() => {
    if (!target) return undefined;
    return (flavor) =>
      target.kind === "opportunity"
        ? host.api.refactoringOpportunityPrompt(target.id, flavor)
        : host.api.refactoringPrompt(target.id, flavor);
  }, [host, target]);
  const forOpportunity = useCallback(
    (o: Pick<RefactoringOpportunity, "opportunity_id" | "file_path">) =>
      setTarget({ kind: "opportunity", id: o.opportunity_id, filePath: o.file_path }),
    [],
  );
  const forPlan = useCallback(
    (p: { id: string; file_path: string }) =>
      setTarget({ kind: "plan", id: p.id, filePath: p.file_path }),
    [],
  );
  return { target, source, forOpportunity, forPlan, close: () => setTarget(null) };
}
