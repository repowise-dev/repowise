"use client";

import { useMemo, type ReactNode } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useTranslations } from "next-intl";
import type { SWRResponse } from "swr";
import type { KnowledgeMatrix, OwnersSummary } from "@repowise-dev/types/owners";
import { ApiError } from "@repowise-dev/ui/shared/api-error";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { OwnershipLede } from "@repowise-dev/ui/owners/ownership-lede";
import { knowledgeCoverage } from "@repowise-dev/ui/owners/knowledge-coverage";
import type { OffboardingScenario } from "@repowise-dev/ui/owners/offboarding-view";
import { AllContributors } from "./all-contributors";
import { useOwnershipLabels } from "./ownership-labels";
import { useKnowledgeMatrix } from "./use-knowledge-matrix";
import { contributorsHref, type ContributorTab } from "./use-contributors-url";

const TabFallback = () => <Skeleton className="h-[50vh] w-full rounded-lg" />;
const KnowledgeRisksView = dynamic(
  () => import("@repowise-dev/ui/owners/knowledge-risks-view").then((m) => m.KnowledgeRisksView),
  { ssr: false, loading: TabFallback },
);
const KnowledgeLossView = dynamic(
  () => import("@repowise-dev/ui/owners/knowledge-loss-view").then((m) => m.KnowledgeLossView),
  { ssr: false, loading: TabFallback },
);
const OffboardingView = dynamic(
  () => import("@repowise-dev/ui/owners/offboarding-view").then((m) => m.OffboardingView),
  { ssr: false, loading: TabFallback },
);

interface PanelProps {
  tab: ContributorTab;
  repoId: string;
  summary: SWRResponse<OwnersSummary>;
  scenario: OffboardingScenario;
  onScenarioChange: (next: OffboardingScenario) => void;
}

/**
 * The active Ownership tab. Every tab reads the summary and the knowledge
 * matrix (one fetch each, shared with the person page); the three matrix
 * views are split out of the landing chunk.
 */
export function ContributorsPanel({ tab, repoId, summary, scenario, onScenarioChange }: PanelProps) {
  const t = useTranslations("views.contributors");
  const tCommon = useTranslations("common");
  const base = `/repos/${repoId}`;
  const matrix = useKnowledgeMatrix(repoId);
  const s = summary.data;
  const labels = useOwnershipLabels();
  const coverage = useMemo(() => (matrix.data ? knowledgeCoverage(matrix.data) : null), [matrix.data]);

  const loadError = (onRetry: () => void, message: string) => (
    <ApiError title={t("loadFailedTitle")} message={message} retryLabel={tCommon("retry")} onRetry={onRetry} />
  );
  const ready = (render: (m: KnowledgeMatrix, s: OwnersSummary) => ReactNode, fallback = <TabFallback />) => {
    if (summary.error) return loadError(() => void summary.mutate(), t("loadFailed"));
    if (matrix.error) return loadError(() => void matrix.mutate(), t("matrixLoadFailed"));
    return matrix.data && s ? render(matrix.data, s) : fallback;
  };

  // The people behind the truck factor, as the one-click starting scenario.
  const suggestions = useMemo(() => {
    if (!s?.truck_factor || s.truck_factor > s.concentration.owners.length) return [];
    const owners = s.concentration.owners.slice(0, s.truck_factor);
    const names = owners.map((o) => o.name).join(", ");
    return [{ label: t("offboardingSuggestion", { names }), keys: owners.map((o) => o.key) }];
  }, [s, t]);

  const panels: Record<ContributorTab, () => ReactNode> = {
    overview: () => (
      <>
        {ready(
          (_, sum) =>
            coverage && (
              <OwnershipLede
                coverage={coverage}
                summary={sum}
                hrefForTab={(next, leave) => contributorsHref(base, next, leave)}
                LinkComponent={Link}
                labels={labels}
              />
            ),
          <Skeleton className="h-40 w-full rounded-lg" />,
        )}
        <AllContributors repoId={repoId} automationCount={s?.automation_identities ?? 0} />
      </>
    ),
    risks: () => ready((m) => <KnowledgeRisksView matrix={m} base={base} LinkComponent={Link} labels={labels} />),
    loss: () =>
      ready((m, sum) => (
        <KnowledgeLossView
          matrix={m}
          summary={sum}
          base={base}
          hrefForPerson={(key) => `${base}/owners/${encodeURIComponent(key)}`}
          hrefForScenario={(key) => contributorsHref(base, "offboarding", [key])}
          LinkComponent={Link}
          labels={labels}
        />
      )),
    offboarding: () =>
      ready((m) => (
        <OffboardingView
          matrix={m}
          scenario={scenario}
          onScenarioChange={onScenarioChange}
          suggestions={suggestions}
          base={base}
          LinkComponent={Link}
          labels={labels}
        />
      )),
  };

  return <div className="flex flex-col gap-6 pt-4 sm:gap-8">{panels[tab]()}</div>;
}
