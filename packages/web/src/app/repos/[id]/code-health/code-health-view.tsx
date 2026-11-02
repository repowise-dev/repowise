"use client";

/**
 * Code Health — `/repos/[id]/code-health`, the client view behind the route's
 * server shell (which redirects tabs that moved out).
 *
 * One living map, a lede that says what the score means, and a thin drill-down.
 * The galaxy map is the page spine; a lens switcher recolors the same field
 * (health / maintainability / performance / churn) so the cross-tab redundancy
 * collapses.
 *
 * Performance has its own causal-opportunity tab; raw observations remain its
 * evidence rather than competing with the ranked finding queue. Hotspots is
 * gone: it rendered a *second* galaxy map
 * on the churn lens directly under this page's, so churn became a lens here and
 * what the lens cannot say — bus factor, the ranked table — became a section
 * under the map. Blast radius keeps its `impact` tab id (existing file-card and
 * symbol-drawer deep links point at it) but is labelled for what it is.
 *
 * Four tabs, defined in `./tabs`: Map, Findings, Performance, Dead code.
 * Tests and Security are pages of their own now, Doc drift a Docs view and
 * blast radius the Commits page's "Review a change".
 *
 * Tabs carry their count where one is cheap to get, so a clean repo says so
 * before you spend a click. Dead code dedupes onto the key its own tab uses.
 * Findings is not numbered: the overview's queue inventory is not the list the
 * tab opens on, and that list's own total is a multi-second query.
 */

import dynamic from "next/dynamic";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo, useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import { HeartPulse, RotateCw } from "lucide-react";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { ReleaseNotice } from "@repowise-dev/ui/shared/release-notice";
import { ViewTabs } from "@repowise-dev/ui/shared/view-tabs";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { Button } from "@repowise-dev/ui/ui/button";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@repowise-dev/ui/ui/select";
import { formatDateTime } from "@repowise-dev/ui/lib/format";
import type { CodeHealthOverlay } from "@repowise-dev/ui/health";
import type { DeadCodeSummary } from "@repowise-dev/types/dead-code";
import {
  HEALTH_COUNTS,
  HEALTH_SCOPES,
  type HealthCounts,
  type HealthScope,
} from "@repowise-dev/types/health";
import { TriageTab } from "@/components/code-health/triage-tab";
import { HotspotsSection } from "@/components/code-health/hotspots-section";
import { TrendSection } from "@/components/code-health/trend-tab";
import { getDeadCodeSummary } from "@/lib/api/dead-code";
import {
  getHealthOverview,
  getHealthTrend,
  getPerformanceOpportunity,
  type HealthOverviewResponse,
  type HealthTrendResponse,
} from "@/lib/api/code-health";
import { useTranslations } from "next-intl";
import {
  CODE_HEALTH_TABS,
  DEFAULT_TAB,
  TAB_DEFS,
  resolveCodeHealthTab,
  type CodeHealthTabId,
} from "./tabs";

// The tabs render one at a time behind `?tab=` and the Map is the default, so
// the others are split out: landing here downloads the Map chunk only.
// ssr:false because they are interactive, data-fetching client views.
const TabFallback = () => <Skeleton className="h-[60vh] w-full rounded-lg" />;
const FindingsTab = dynamic(
  () => import("@/components/code-health/findings-tab").then((m) => m.FindingsTab),
  { ssr: false, loading: TabFallback },
);
const PerformanceTab = dynamic(
  () => import("@/components/code-health/performance-tab").then((m) => m.PerformanceTab),
  { ssr: false, loading: TabFallback },
);
const DeadCodeTab = dynamic(
  () => import("@/components/risk/dead-code-tab").then((m) => m.DeadCodeTab),
  { ssr: false, loading: TabFallback },
);

/**
 * Lenses on the map. Three lead as segments; the rest sit behind "More",
 * where later lenses (off-boarding, coupling) join. Churn percentiles are
 * joined in by the map tab; owner and knowledge read the ownership rollup.
 */
const PRIMARY_LENSES: CodeHealthOverlay[] = ["health", "hotspot", "owner"];
const OVERLAYS: CodeHealthOverlay[] = [
  ...PRIMARY_LENSES,
  "maintainability",
  "performance",
  "coverage",
  "churn",
  "knowledge",
];

/**
 * Tabs whose data honours `scope`. Dead code has no production/test split,
 * and performance carries its own execution-context control, so the toggle is
 * offered where it does something rather than sitting inert on the others.
 */
const SCOPED_TABS: CodeHealthTabId[] = ["map", "findings"];

const SCOPE_LABEL_KEYS: Record<HealthScope, string> = {
  all: "scopeAll",
  production: "scopeProduction",
};

/**
 * Tabs whose data honours `counts`. The trend is deliberately absent even
 * though it sits on the Overview: snapshots recorded the full score, so there
 * is no code-shape series to draw and inventing one from mean deductions would
 * disagree with the headline wherever a file sits at the score floor.
 */
const COUNTED_TABS: CodeHealthTabId[] = ["map", "findings"];

/**
 * A named dropdown for a view control in the page header.
 *
 * These are filters over everything on the page, not navigation, so they read
 * as a question and its current answer rather than as a second row of tabs
 * competing with the real one.
 */
function ViewSelect({
  label,
  value,
  onValueChange,
  options,
}: {
  label: string;
  value: string;
  onValueChange: (next: string) => void;
  options: { id: string; label: string }[];
}) {
  return (
    <div className="flex min-w-0 items-center gap-2">
      <span className="shrink-0 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        {label}
      </span>
      <Select value={value} onValueChange={onValueChange}>
        <SelectTrigger aria-label={label} className="h-8 w-auto gap-1.5 px-2.5 text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o.id} value={o.id}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

const COUNTS_LABEL_KEYS: Record<HealthCounts, string> = {
  everything: "countsEverything",
  code_shape: "countsCodeShape",
};

export function CodeHealthView() {
  const t = useTranslations("views.codeHealth");
  const tc = useTranslations("common");
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const repoId = params.id;

  // `?pillar=` predates the lens and the performance tab: the Overview health
  // card still links with it. Performance now has a tab of its own and the
  // other two are lenses, so the parameter resolves onto whichever surface
  // actually answers it rather than onto a control that no longer exists.
  const rawPillar = searchParams.get("pillar");

  const activeTab = resolveCodeHealthTab(searchParams.get("tab"), rawPillar);

  const rawScope = searchParams.get("scope");
  const scope: HealthScope = (HEALTH_SCOPES as readonly string[]).includes(rawScope ?? "")
    ? (rawScope as HealthScope)
    : "all";
  // Only the default population needs no key suffix, so an existing cache entry
  // stays valid and the narrowed one gets its own.
  const scopeKey = scope === "all" ? "" : `:${scope}`;

  const rawCounts = searchParams.get("counts");
  const counts: HealthCounts = (HEALTH_COUNTS as readonly string[]).includes(rawCounts ?? "")
    ? (rawCounts as HealthCounts)
    : "everything";
  // Same convention as scope, and appended after it: the two suffixes have to
  // compose in one fixed order or the page-level key and the view-level key
  // stop matching and the overview is fetched twice.
  const countsKey = counts === "everything" ? "" : `:${counts}`;
  const viewKey = `${scopeKey}${countsKey}`;

  const rawLens = searchParams.get("lens");
  const overlay: CodeHealthOverlay = (OVERLAYS as readonly string[]).includes(rawLens ?? "")
    ? (rawLens as CodeHealthOverlay)
    : rawPillar === "maintainability"
      ? "maintainability"
      : "health";

  // The map's selection and its opportunity highlight live in the URL, so a
  // link from the performance queue opens this field on the file it is about.
  const selectedPath = searchParams.get("file");
  const opportunityId = searchParams.get("opportunity");

  // Shares the SWR key with TriageView, so the meta line costs no extra request.
  const { data: overview } = useSWR<HealthOverviewResponse>(
    `code-health-overview:${repoId}${viewKey}`,
    () => getHealthOverview(repoId, 25, scope, counts),
    { revalidateOnFocus: false },
  );
  const meta = overview?.meta;

  // Refresh revalidates every SWR key for this repo (overview + each tab's own
  // keys), so the button works on whichever tab is active.
  const { mutate: mutateAll } = useSWRConfig();
  const [refreshing, setRefreshing] = useState(false);
  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await mutateAll((key) => typeof key === "string" && key.includes(repoId), undefined, {
        revalidate: true,
      });
    } finally {
      setRefreshing(false);
    }
  }, [mutateAll, repoId]);

  // Trend fetched ONCE here, fed to the folded Trend section — no second fetch.
  const {
    data: trend,
    isLoading: trendLoading,
    error: trendError,
    mutate: mutateTrend,
  } = useSWR<HealthTrendResponse>(
    `code-health-trend:${repoId}${scopeKey}`,
    () => getHealthTrend(repoId, 20, scope),
    { revalidateOnFocus: false },
  );

  // The opportunity a link arrived with, so its files can be guaranteed a node
  // and marked. One bounded request, and only when the link carries an id.
  const { data: opportunity } = useSWR(
    opportunityId ? `code-health-map-opportunity:${repoId}:${opportunityId}` : null,
    () => getPerformanceOpportunity(repoId, opportunityId as string, { evidenceLimit: 25 }),
    { revalidateOnFocus: false },
  );

  // The intervention file plus the files its evidence sits in. An id from a
  // retired model resolves to a state rather than to a row, and that shape
  // carries no paths, so it simply marks nothing.
  const highlightPaths = useMemo(() => {
    if (!opportunity || !("file_path" in opportunity)) return undefined;
    const paths = [opportunity.file_path, ...opportunity.evidence.map((e) => e.file_path)];
    return [...new Set(paths.filter(Boolean))];
  }, [opportunity]);

  // ---- Tab counts ----
  // Dead code uses the same key + fetcher as its own tab, so this is a prefetch
  // rather than a duplicate request.
  const { data: deadCode } = useSWR<DeadCodeSummary>(
    `dead-code-summary:${repoId}`,
    () => getDeadCodeSummary(repoId),
    { revalidateOnFocus: false },
  );
  const badges: Partial<Record<CodeHealthTabId, number>> = {};
  if (deadCode) badges["dead-code"] = deadCode.total_findings;

  const setTab = useCallback(
    (next: string) => {
      const sp = new URLSearchParams(searchParams.toString());
      if (next === DEFAULT_TAB) sp.delete("tab");
      else sp.set("tab", next);
      const qs = sp.toString();
      router.replace(qs ? `?${qs}` : "?", { scroll: false });
    },
    [router, searchParams],
  );

  const setSelectedPath = useCallback(
    (next: string | null) => {
      const sp = new URLSearchParams(searchParams.toString());
      if (next) sp.set("file", next);
      else sp.delete("file");
      const qs = sp.toString();
      router.replace(qs ? `?${qs}` : "?", { scroll: false });
    },
    [router, searchParams],
  );

  const setScope = useCallback(
    (next: string) => {
      const sp = new URLSearchParams(searchParams.toString());
      if (next === "all") sp.delete("scope");
      else sp.set("scope", next);
      const qs = sp.toString();
      router.replace(qs ? `?${qs}` : "?", { scroll: false });
    },
    [router, searchParams],
  );

  const setCounts = useCallback(
    (next: string) => {
      const sp = new URLSearchParams(searchParams.toString());
      if (next === "everything") sp.delete("counts");
      else sp.set("counts", next);
      const qs = sp.toString();
      router.replace(qs ? `?${qs}` : "?", { scroll: false });
    },
    [router, searchParams],
  );

  const setOverlay = useCallback(
    (next: CodeHealthOverlay) => {
      const sp = new URLSearchParams(searchParams.toString());
      if (next === "health") sp.delete("lens");
      else sp.set("lens", next);
      const qs = sp.toString();
      router.replace(qs ? `?${qs}` : "?", { scroll: false });
    },
    [router, searchParams],
  );

  return (
    <PageShell
      title={t("title")}
      icon={<HeartPulse />}
      // What the page is for; the lede below says what the score is built from.
      description={t("description")}
      // "wide" rather than the style's usual 1280 — a deliberate divergence. At
      // 1280 the map keeps ~900px beside its 320px inspector; the extra width
      // goes entirely to the field, which is this page's whole subject.
      maxWidth="wide"
      actions={
        // Two named dropdowns rather than two tab rows. Four choices spelled
        // out in full overflowed the header on a phone and set the whole page
        // scrolling sideways; a trigger shows the current answer and spends no
        // width on the alternative.
        <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2">
          {SCOPED_TABS.includes(activeTab) && (
            <ViewSelect
              label={t("scopeFiles")}
              value={scope}
              onValueChange={setScope}
              options={HEALTH_SCOPES.map((id) => ({
                id,
                label: t(SCOPE_LABEL_KEYS[id]),
              }))}
            />
          )}
          {COUNTED_TABS.includes(activeTab) && (
            <ViewSelect
              label={t("scopeCounts")}
              value={counts}
              onValueChange={setCounts}
              options={HEALTH_COUNTS.map((id) => ({
                id,
                label: t(COUNTS_LABEL_KEYS[id]),
              }))}
            />
          )}
          <Button size="sm" variant="outline" onClick={refresh} disabled={refreshing}>
            <RotateCw
              className={`mr-1.5 h-3.5 w-3.5 ${refreshing ? "motion-safe:animate-spin" : ""}`}
            />{" "}
            {refreshing ? t("refreshing") : t("refresh")}
          </Button>
        </div>
      }
    >
      {/* Replaced each release that moves health scores, and keyed with that
          release's version so a reader who dismissed an earlier one sees it. */}
      <ReleaseNotice
        id="health-scoring"
        version="0.60.0"
        detail={t("coChangeNoticeBody")}
        detailLabel={t("coChangeNoticeMore")}
        hideLabel={t("coChangeNoticeLess")}
        dismissLabel={tc("dismiss")}
      >
        {t("coChangeNoticeTitle")}
      </ReleaseNotice>

      {meta ? (
        <p className="-mt-3 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
          {meta.last_indexed_at
            ? t("indexed", { date: formatDateTime(meta.last_indexed_at) })
            : t("notIndexed")}
          {meta.head_commit ? ` · ${meta.head_commit.slice(0, 8)}` : ""}
          {` · ${t("snapshots", { count: meta.snapshot_count })}`}
        </p>
      ) : null}

      <ViewTabs
        tabs={CODE_HEALTH_TABS.map((id) => {
          const Icon = TAB_DEFS[id].icon;
          return {
            id,
            label: t(TAB_DEFS[id].labelKey),
            icon: <Icon className="h-3.5 w-3.5" />,
            ...(badges[id] !== undefined ? { badge: badges[id] } : {}),
          };
        })}
        value={activeTab}
        onValueChange={setTab}
      >
        {activeTab === "map" && (
          <TriageTab
              counts={counts}
            repoId={repoId}
            trend={trend}
            overlay={overlay}
            onOverlayChange={setOverlay}
            lenses={OVERLAYS}
            primaryLenses={PRIMARY_LENSES}
            selectedPath={selectedPath}
            onSelectPath={setSelectedPath}
            highlightPaths={highlightPaths}
            scope={scope}
            hotspotsSlot={<HotspotsSection repoId={repoId} />}
            trendSlot={
              <OverviewSection
                title={t("trendTitle")}
                description={t("trendDescription")}
              >
                <TrendSection
                  data={trend}
                  isLoading={trendLoading}
                  error={trendError}
                  onRetry={() => void mutateTrend()}
                  counts={counts}
                  onSelectFile={setSelectedPath}
                />
              </OverviewSection>
            }
          />
        )}
        {activeTab === "findings" && <FindingsTab repoId={repoId} scope={scope} counts={counts} />}
        {activeTab === "performance" && <PerformanceTab repoId={repoId} />}
        {activeTab === "dead-code" && <DeadCodeTab repoId={repoId} />}
      </ViewTabs>
    </PageShell>
  );
}
