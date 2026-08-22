"use client";

/**
 * Refactoring — `/repos/[id]/refactoring`.
 *
 * The deterministic refactoring plans the health pass writes. The page leads
 * with what the pile actually is (mostly small, local extractions), then the
 * handful that change the codebase's shape, then everything as rows.
 *
 * Two URL params, both shareable: `?type=` filters the list, `?plan=` opens one
 * plan's drawer. The plan param is what the old centred modal could not have —
 * a plan you can send to someone.
 */

import { use, useCallback, useDeferredValue, useMemo, useState } from "react";
import useSWR from "swr";
import { parseAsString, parseAsStringLiteral, useQueryState } from "nuqs";
import { Wrench, RotateCw } from "lucide-react";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { ViewTabs } from "@repowise-dev/ui/shared/view-tabs";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import {
  RefactoringBoard,
  RefactoringDrawer,
  STRUCTURAL_TYPES,
  TYPE_ORDER,
  typeMeta,
  type RefactoringSortKey,
} from "@repowise-dev/ui/refactoring";
import type {
  Confidence,
  EffortBucket,
  RefactoringPlan,
  RefactoringPlanPage,
} from "@repowise-dev/types/refactoring";
import { AiPromptModal, buildRefactoringPlanPrompt } from "@repowise-dev/ui/health";
import {
  generateRefactoringCode,
  getRefactoringSettings,
  getRefactoringPlan,
  getRefactoringPlansPage,
  getRefactoringTargets,
  type RefactoringSettings,
} from "@/lib/api/refactoring";

const TYPE_VALUES = ["all", "structural", ...TYPE_ORDER] as const;
type TypeFilter = (typeof TYPE_VALUES)[number];
type RefactoringPageData = RefactoringPlanPage & { legacy?: boolean };

export default function RefactoringPage({ params }: { params: Promise<{ id: string }> }) {
  const { id: repoId } = use(params);
  const [type, setType] = useQueryState(
    "type",
    parseAsStringLiteral(TYPE_VALUES).withDefault("all"),
  );
  const [openPlanId, setOpenPlanId] = useQueryState("plan", parseAsString);
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query);
  const [sort, setSort] = useState<RefactoringSortKey>("canonical");
  const [efforts, setEfforts] = useState<EffortBucket[]>([]);
  const [confidences, setConfidences] = useState<Confidence[]>([]);
  const [offset, setOffset] = useState(0);

  const { data, error, isLoading, mutate } = useSWR<RefactoringPageData>(
    [
      "refactoring-page",
      repoId,
      type,
      deferredQuery,
      sort,
      efforts.join(","),
      confidences.join(","),
      offset,
    ],
    async () => {
      try {
        return await getRefactoringPlansPage(repoId, {
          refactoringType: type === "all" ? undefined : type,
          search: deferredQuery || undefined,
          sort,
          effort: efforts.join(",") || undefined,
          confidence: confidences.join(",") || undefined,
          limit: 60,
          offset,
        });
      } catch (caught) {
        const status = Number((caught as { status?: number }).status);
        if (status !== 404 && status !== 405) throw caught;
        const legacy = await getRefactoringTargets(repoId, {
          refactoringType:
            type === "all" || type === "structural" ? undefined : type,
        });
        const items =
          type === "structural"
            ? legacy.plans.filter((plan) =>
                (STRUCTURAL_TYPES as readonly string[]).includes(plan.refactoring_type),
              )
            : legacy.plans;
        return {
          items,
          total: items.length,
          has_more: false,
          next_offset: null,
          summary: legacy.summary,
          structural_leads: legacy.plans
            .filter((plan) =>
              (STRUCTURAL_TYPES as readonly string[]).includes(plan.refactoring_type),
            )
            .slice(0, 12),
          legacy: true,
        };
      }
    },
    { revalidateOnFocus: false, keepPreviousData: true },
  );

  const plans = useMemo(() => data?.items ?? [], [data?.items]);

  const prefix = `/repos/${repoId}`;
  // `PlanRows` offers a second `line` argument and this deliberately ignores
  // it: the file page renders no line anchors, so appending `#L<n>` would be a
  // fragment that resolves to nothing and leaves the reader at the top of the
  // page wondering what they missed. Honour it here when the page grows them.
  const fileHref = useCallback((path: string) => fileEntityPath(prefix, path), [prefix]);

  // The open plan comes from the URL, so a reload or a shared link lands on the
  // same drawer rather than the top of the list.
  const openPlanOnPage = useMemo(
    () => plans.find((p) => p.id === openPlanId) ?? null,
    [plans, openPlanId],
  );
  const { data: linkedPlan } = useSWR<RefactoringPlan>(
    openPlanId && !openPlanOnPage ? ["refactoring-plan", repoId, openPlanId] : null,
    () => getRefactoringPlan(repoId, openPlanId!),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  );
  const openPlan = openPlanOnPage ?? linkedPlan ?? null;

  const [promptPlan, setPromptPlan] = useState<RefactoringPlan | null>(null);
  const onAiPrompt = useCallback((plan: RefactoringPlan) => setPromptPlan(plan), []);

  // Opt-in code generation. Enabled only when the repo's config turns it on
  // (a local-`serve` capability); the settings call 404s on hosted backends,
  // which simply leaves the action hidden.
  const { data: settings } = useSWR<RefactoringSettings>(
    `refactoring-settings:${repoId}`,
    () => getRefactoringSettings(repoId),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  );
  const onGenerateCode = useMemo(
    () =>
      settings?.enabled
        ? (plan: RefactoringPlan) => generateRefactoringCode(repoId, plan.id)
        : undefined,
    [settings?.enabled, repoId],
  );

  const counts = new Map((data?.summary.by_type ?? []).map((c) => [c.type, c.count]));
  const structuralCount = (STRUCTURAL_TYPES as readonly string[]).reduce(
    (n, t) => n + (counts.get(t) ?? 0),
    0,
  );
  const tabs = [
    { id: "all" as const, label: "All", badge: data?.summary.total },
    { id: "structural" as const, label: "Structural", badge: structuralCount },
    ...TYPE_ORDER.map((t) => ({
      id: t,
      label: typeMeta(t).label,
      badge: counts.get(t) ?? 0,
    })),
  ].filter((t) => t.id === "all" || (t.badge ?? 0) > 0);

  return (
    <PageShell
      title="Refactoring"
      icon={<Wrench className="h-5 w-5 text-[var(--color-accent-primary)]" />}
      description="Concrete, ranked plans the health pass wrote from your code. Open one to see the change, or hand it to a coding agent."
      actions={
        <button
          type="button"
          onClick={() => void mutate()}
          className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-2.5 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:text-[var(--color-text-primary)]"
        >
          <RotateCw className="h-3.5 w-3.5" />
          Refresh
        </button>
      }
    >
      <div className="space-y-6">
        <ViewTabs
          tabs={tabs}
          value={type}
          onValueChange={(id) => {
            setOffset(0);
            void setType(id as TypeFilter);
          }}
        />

        {error ? (
          <div className="rounded-2xl border border-[var(--color-error)]/30 bg-[var(--color-error)]/5 p-6 text-sm text-[var(--color-text-secondary)]">
            Couldn&apos;t load refactoring plans. The repo may not be indexed yet, or the API is
            unreachable.
          </div>
        ) : isLoading ? (
          // Matches the real layout's shapes: a lede block, a ribbon, a field.
          <div className="space-y-8">
            <div className="h-32 animate-pulse rounded-xl bg-[var(--color-bg-surface)]" />
            <div className="h-16 animate-pulse rounded-xl bg-[var(--color-bg-surface)]" />
            <div className="h-72 animate-pulse rounded-xl bg-[var(--color-bg-surface)]" />
          </div>
        ) : (
          <RefactoringBoard
            plans={plans}
            allPlans={data?.legacy ? data.items : data?.structural_leads}
            structuralPlans={
              data?.legacy
                ? data.items.filter((plan) =>
                    (STRUCTURAL_TYPES as readonly string[]).includes(plan.refactoring_type),
                  )
                : data?.structural_leads
            }
            summary={data?.summary}
            serverState={
              data?.legacy
                ? undefined
                : {
                    query,
                    sort,
                    efforts,
                    confidences,
                    total: data?.total ?? 0,
                    offset,
                    nextOffset: data?.next_offset ?? null,
                  }
            }
            onServerStateChange={(change) => {
              if (change.query !== undefined) setQuery(change.query);
              if (change.sort !== undefined) setSort(change.sort);
              if (change.efforts !== undefined) setEfforts(change.efforts);
              if (change.confidences !== undefined) setConfidences(change.confidences);
              if (change.offset !== undefined) setOffset(change.offset);
            }}
            onOpen={(plan) => void setOpenPlanId(plan.id)}
            onAiPrompt={onAiPrompt}
            onSeeStructural={() => {
              setOffset(0);
              void setType("structural");
            }}
            fileHref={fileHref}
            // The lede and Start here describe the whole repo, so they only
            // belong on the unfiltered view — under a type filter they would
            // be talking about a set the list below is not showing.
            showLede={type === "all"}
            sectionTitle={
              type === "all"
                ? "All plans"
                : type === "structural"
                  ? "Structural plans"
                  : `${typeMeta(type).label} plans`
            }
          />
        )}
      </div>

      <RefactoringDrawer
        plan={openPlan}
        open={openPlan !== null}
        onOpenChange={(open) => {
          if (!open) void setOpenPlanId(null);
        }}
        onAiPrompt={onAiPrompt}
        onGenerateCode={onGenerateCode}
        settingsHref={`${prefix}/settings`}
        fileHref={fileHref}
      />

      <AiPromptModal
        open={promptPlan !== null}
        onOpenChange={(open) => {
          if (!open) setPromptPlan(null);
        }}
        getPrompt={
          promptPlan
            ? (flavor) => buildRefactoringPlanPrompt({ plan: promptPlan, flavor })
            : null
        }
        filePath={promptPlan?.file_path ?? null}
        title="AI refactoring prompt"
        description="A ready-to-paste plan that hands your AI coding agent the exact steps, the blast radius to keep consistent, and a completion contract."
      />
    </PageShell>
  );
}
