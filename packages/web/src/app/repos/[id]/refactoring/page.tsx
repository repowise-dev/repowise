"use client";

/**
 * Refactoring — `/repos/[id]/refactoring`.
 *
 * The composed opportunities the health pass writes: one per file, each a set
 * of ordered steps. The page leads with what the pile actually is, then the
 * handful that change the codebase's shape, then everything as rows.
 *
 * Three URL params, all shareable: `?type=` filters the list, `?opportunity=`
 * opens one opportunity's drawer, and `?plan=` opens a single step in the plan
 * inspector. The plan param is kept because the performance queue deep-links
 * into it by plan id, and because a step is still a thing worth linking to.
 */

import { use, useCallback, useDeferredValue, useMemo, useState } from "react";
import useSWR from "swr";
import { useRouter } from "next/navigation";
import { parseAsString, parseAsStringLiteral, useQueryState } from "nuqs";
import { useTranslations } from "next-intl";
import { Wrench, RotateCw } from "lucide-react";
import { ApiError } from "@repowise-dev/ui/shared/api-error";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { ViewTabs } from "@repowise-dev/ui/shared/view-tabs";

const REFACTORING_PANEL_ID = "refactoring-tab-panel";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
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
import type {
  Confidence,
  EffortBucket,
  OpportunityStatus,
  RefactoringOpportunity,
  RefactoringOpportunityDetail,
  RefactoringOpportunityDetailResolved,
  RefactoringOpportunityPage,
  RefactoringOrder,
  RefactoringPlan,
  RefactoringScope,
} from "@repowise-dev/types/refactoring";
import {
  useRelatedWork,
  AiPromptModal,
  buildRefactoringOpportunityPrompt,
  buildRefactoringPlanPrompt,
} from "@repowise-dev/ui/health";
import {
  generateRefactoringCode,
  getRefactoringOpportunities,
  getRefactoringOpportunity,
  getRefactoringPlan,
  getRefactoringSettings,
  updateRefactoringSettings,
  updateRefactoringOpportunityStatus,
  type RefactoringSettings,
} from "@/lib/api/refactoring";
import { getFileContent } from "@/lib/api/files";
import { getRelatedWork, relatedWorkHref } from "@/lib/api/related-work";
import { providerSetupHref } from "@/lib/utils/page-href";

const TYPE_VALUES = ["all", "structural", ...TYPE_ORDER] as const;
type TypeFilter = (typeof TYPE_VALUES)[number];

const PAGE_SIZE = 60;
const STRUCTURAL_CSV = (STRUCTURAL_TYPES as readonly string[]).join(",");

/** The list's type tab, as the one lead type or the structural set. */
function leadTypeFor(type: TypeFilter): string | undefined {
  if (type === "all") return undefined;
  if (type === "structural") return STRUCTURAL_CSV;
  return type;
}

export default function RefactoringPage({ params }: { params: Promise<{ id: string }> }) {
  const { id: repoId } = use(params);
  const router = useRouter();
  const t = useTranslations("views.refactoring");
  const tCommon = useTranslations("common");
  const [type, setType] = useQueryState(
    "type",
    parseAsStringLiteral(TYPE_VALUES).withDefault("all"),
  );
  const [openId, setOpenId] = useQueryState("opportunity", parseAsString);
  const [openPlanId, setOpenPlanId] = useQueryState("plan", parseAsString);

  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query);
  const [order, setOrder] = useState<RefactoringOrder>("queue");
  const [status, setStatus] = useState<OpportunityStatus>("open");
  const [effort, setEffort] = useState<EffortBucket | null>(null);
  const [confidence, setConfidence] = useState<Confidence | null>(null);
  const [mechanicalOnly, setMechanicalOnly] = useState(false);
  // What Fix first would take, by default; the full inventory on request.
  const [scope, setScope] = useState<RefactoringScope>("fix_first");
  const [offset, setOffset] = useState(0);

  const { data, error, isLoading, mutate } = useSWR<RefactoringOpportunityPage>(
    [
      "refactoring-opportunities",
      repoId,
      type,
      deferredQuery,
      order,
      status,
      effort,
      confidence,
      mechanicalOnly,
      scope,
      offset,
    ],
    () =>
      getRefactoringOpportunities(repoId, {
        refactoringType: leadTypeFor(type),
        status,
        scope,
        search: deferredQuery || undefined,
        effort: effort ?? undefined,
        confidence: confidence ?? undefined,
        mechanical: mechanicalOnly,
        order,
        // The row renders step counts, not steps. Asking for the steps would
        // roughly double the page for pixels nothing draws.
        stepPreview: 0,
        limit: PAGE_SIZE,
        offset,
      }),
    { revalidateOnFocus: false, keepPreviousData: true },
  );

  // The structural head for "Start here". A separate bounded call rather than a
  // slice of the list above: the list is under whatever filter the reader chose,
  // and Start here describes the whole repository, in the scope the list is in.
  const { data: structural } = useSWR<RefactoringOpportunityPage>(
    type === "all" ? ["refactoring-structural", repoId, scope] : null,
    () =>
      getRefactoringOpportunities(repoId, {
        refactoringType: STRUCTURAL_CSV,
        scope,
        order: "rank",
        stepPreview: 0,
        limit: 100,
      }),
    { revalidateOnFocus: false },
  );

  const opportunities = useMemo(() => data?.items ?? [], [data?.items]);
  const prefix = `/repos/${repoId}`;
  const fileHref = useCallback((path: string) => fileEntityPath(prefix, path), [prefix]);
  // The read the file view makes, for a step's inline excerpt.
  const readSource = useCallback((path: string) => getFileContent(repoId, path), [repoId]);

  // The open opportunity comes from the URL, so a reload or a shared link lands
  // on the same drawer rather than the top of the list.
  const { data: openDetail, isLoading: detailLoading } = useSWR<RefactoringOpportunityDetail>(
    openId ? ["refactoring-opportunity", repoId, openId] : null,
    () => getRefactoringOpportunity(repoId, openId!, { stepLimit: 50, evidenceLimit: 20 }),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  );

  // What the other lenses hold for the open opportunity's file.
  const openFile = openDetail?.found ? openDetail.file_path : null;
  const fetchRelated = useCallback(
    (paths: string[]) => getRelatedWork(repoId, paths),
    [repoId],
  );
  const related = useRelatedWork(fetchRelated, [openFile]);
  const toRelated = useMemo(() => relatedWorkHref(repoId), [repoId]);

  const { data: openPlan } = useSWR<RefactoringPlan>(
    openPlanId ? ["refactoring-plan", repoId, openPlanId] : null,
    () => getRefactoringPlan(repoId, openPlanId!),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  );

  const [promptFor, setPromptFor] = useState<
    | { kind: "opportunity"; value: RefactoringOpportunityDetailResolved }
    | { kind: "plan"; value: RefactoringPlan }
    | null
  >(null);

  // A row hands over its whole opportunity, which means fetching the detail the
  // list deliberately does not carry. One call, on demand, rather than steps on
  // every row of every page.
  const onRowAiPrompt = useCallback(
    async (opportunity: RefactoringOpportunity) => {
      const detail = await getRefactoringOpportunity(repoId, opportunity.opportunity_id, {
        stepLimit: 50,
        evidenceLimit: 20,
      });
      if (detail.found) setPromptFor({ kind: "opportunity", value: detail });
    },
    [repoId],
  );

  const onStatusChange = useCallback(
    async (opportunity: { opportunity_id: string }, status: OpportunityStatus) => {
      await updateRefactoringOpportunityStatus(repoId, opportunity.opportunity_id, status);
      // The row's optimistic state stands until this lands; refetching is what
      // makes the server the authority on what the state actually became, since
      // an opportunity's status is a rollup and not simply what was asked for.
      await mutate();
    },
    [repoId, mutate],
  );

  // Opt-in code generation (a local-`serve` capability). The action shows only
  // when the switch is on and a model resolves; otherwise the plan drawer shows
  // the switch, with a setup link when no model is configured. The settings
  // call 404s on hosted backends, which hides both.
  const { data: settings, mutate: mutateSettings } = useSWR<RefactoringSettings>(
    `refactoring-settings:${repoId}`,
    () => getRefactoringSettings(repoId),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  );
  const onGenerateCode = useMemo(
    () =>
      settings?.enabled && settings.provider
        ? (plan: RefactoringPlan) => generateRefactoringCode(repoId, plan.id)
        : undefined,
    [settings?.enabled, settings?.provider, repoId],
  );
  const modelSetting = useMemo(
    () =>
      settings
        ? {
            value: settings,
            onToggle: async (enabled: boolean) => {
              await mutateSettings(await updateRefactoringSettings(repoId, enabled), {
                revalidate: false,
              });
            },
            setupHref: providerSetupHref(repoId),
          }
        : undefined,
    [settings, mutateSettings, repoId],
  );

  const facetCounts = data?.facets?.lead_type ?? {};
  const summary = data?.summary ?? null;
  const facetTotal = Object.values(facetCounts).reduce((n, c) => n + c, 0);
  const structuralCount = (STRUCTURAL_TYPES as readonly string[]).reduce(
    (n, t) => n + (facetCounts[t] ?? 0),
    0,
  );
  const tabs = [
    // Summed from the facets, not from the rollup: the facets follow the status
    // filter and the rollup does not, so under "Resolved" the rollup would put
    // the open total on a tab that lists resolved rows.
    { id: "all" as const, label: t("tabAll"), badge: facetTotal },
    { id: "structural" as const, label: t("tabStructural"), badge: structuralCount },
    ...TYPE_ORDER.filter((t) => t !== "performance_fix").map((t) => ({
      id: t,
      label: typeMeta(t).label,
      badge: facetCounts[t] ?? 0,
    })),
  ].filter((tab) => tab.id === "all" || (tab.badge ?? 0) > 0);

  const serverState: RefactoringBoardServerState = {
    query,
    order,
    status,
    effort,
    confidence,
    mechanicalOnly,
    scope,
    appliedScope: data?.scope ?? "all",
    hidden: data?.hidden ?? null,
    counts: data?.counts ?? null,
    total: data?.total ?? 0,
    offset,
    nextOffset: data?.next_offset ?? null,
  };

  return (
    <PageShell
      title={t("title")}
      icon={<Wrench />}
      description={t("description")}
      actions={
        <button
          type="button"
          onClick={() => void mutate()}
          className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-2.5 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:text-[var(--color-text-primary)]"
        >
          <RotateCw className="h-3.5 w-3.5" />
          {tCommon("refresh")}
        </button>
      }
    >
      <div className="space-y-6">
        <ViewTabs
          tabs={tabs}
          value={type}
          panelId={REFACTORING_PANEL_ID}
          onValueChange={(id) => {
            setOffset(0);
            void setType(id as TypeFilter);
          }}
        />

        <div
          id={REFACTORING_PANEL_ID}
          role="tabpanel"
          aria-labelledby={`${REFACTORING_PANEL_ID}-tab-${type}`}
          tabIndex={0}
        >
        {error ? (
          <ApiError
            title={t("loadFailedTitle")}
            message={t("loadFailed")}
            retryLabel={tCommon("retry")}
            onRetry={() => void mutate()}
          />
        ) : data?.summary?.status === "unavailable" ? (
          <EmptyState
            title={t("unavailableTitle")}
            description={t("unavailableDescription")}
          >
            {data.summary.detail ? (
              <p className="mt-2 text-xs text-[var(--color-text-tertiary)]">{data.summary.detail}</p>
            ) : null}
          </EmptyState>
        ) : isLoading ? (
          // Matches the real layout's shapes: a lede block, a ribbon, a field.
          <SkeletonRegion className="space-y-8" label={t("loading")}>
            <Skeleton className="h-32 rounded-xl" />
            <Skeleton className="h-16 rounded-xl" />
            <Skeleton className="h-72 rounded-xl" />
          </SkeletonRegion>
        ) : (
          <RefactoringBoard
            opportunities={opportunities}
            summary={summary}
            facets={data?.facets}
            structuralOpportunities={structural?.items}
            serverState={serverState}
            onServerStateChange={(change) => {
              if (change.query !== undefined) setQuery(change.query);
              if (change.order !== undefined) setOrder(change.order);
              if (change.status !== undefined) setStatus(change.status);
              if (change.effort !== undefined) setEffort(change.effort);
              if (change.confidence !== undefined) setConfidence(change.confidence);
              if (change.mechanicalOnly !== undefined) setMechanicalOnly(change.mechanicalOnly);
              if (change.scope !== undefined) setScope(change.scope);
              if (change.offset !== undefined) setOffset(change.offset);
            }}
            onOpen={(o) => void setOpenId(o.opportunity_id)}
            onAiPrompt={(o) => void onRowAiPrompt(o)}
            onStatusChange={onStatusChange}
            onSeeStructural={() => {
              setOffset(0);
              void setType("structural");
            }}
            fileHref={fileHref}
            // The lede and Start here describe the whole repo, so they only
            // belong on the unfiltered view — under a type filter they would be
            // talking about a set the list below is not showing.
            showLede={type === "all"}
            sectionTitle={
              type === "all"
                ? serverState.appliedScope === "fix_first"
                  ? t("sectionTitleFixFirst")
                  : t("sectionTitleAll")
                : type === "structural"
                  ? t("sectionTitleStructural")
                  : t("sectionTitleFiltered", { type: typeMeta(type).label })
            }
          />
        )}
        </div>
      </div>

      <OpportunityDrawer
        detail={openDetail ?? null}
        open={openId !== null}
        loading={detailLoading}
        onOpenChange={(open) => {
          if (!open) void setOpenId(null);
        }}
        onAiPrompt={(detail) => setPromptFor({ kind: "opportunity", value: detail })}
        onStatusChange={onStatusChange}
        onOpenStep={(planId) => void setOpenPlanId(planId)}
        fileHref={fileHref}
        readSource={readSource}
        onGenerateCode={onGenerateCode}
        related={related?.files?.[0]}
        relatedWorkHref={toRelated}
        onNavigate={(href) => router.push(href)}
      />

      <RefactoringDrawer
        plan={openPlan ?? null}
        open={openPlanId !== null}
        onOpenChange={(open) => {
          if (!open) void setOpenPlanId(null);
        }}
        onAiPrompt={(plan) => setPromptFor({ kind: "plan", value: plan })}
        onGenerateCode={onGenerateCode}
        settingsHref={`${prefix}/settings`}
        modelSetting={modelSetting}
        fileHref={fileHref}
      />

      <AiPromptModal
        open={promptFor !== null}
        onOpenChange={(open) => {
          if (!open) setPromptFor(null);
        }}
        getPrompt={
          promptFor
            ? (flavor) =>
                promptFor.kind === "opportunity"
                  ? buildRefactoringOpportunityPrompt({
                      opportunity: promptFor.value,
                      flavor,
                    })
                  : buildRefactoringPlanPrompt({ plan: promptFor.value, flavor })
            : null
        }
        filePath={promptFor?.value.file_path ?? null}
        title={t("promptTitle")}
        description={t("promptDescription")}
      />
    </PageShell>
  );
}
