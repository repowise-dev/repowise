import { useMemo } from "react";
import { useTranslations } from "next-intl";
import type { AreaCouplingCopyOverrides } from "@repowise-dev/ui/coupling";

/** The area coupling view's copy from `coupling.area.*`, stable per locale. */
export function useAreaCouplingCopy(): AreaCouplingCopyOverrides {
  const t = useTranslations("coupling.area");
  return useMemo(
    () => ({
      rootLabel: t("rootLabel"),
      ledeLabel: t("ledeLabel"),
      ledeUnit: (count: number) => t("ledeUnit", { count }),
      verdict: (count: number) => t("verdict", { count }),
      scope: (pairs: number, depth: number, testsIncluded: boolean) =>
        t(testsIncluded ? "scopeWithTests" : "scope", { pairs, depth }),
      cleanTitle: (depth: number) => t("cleanTitle", { depth }),
      cleanBody: (pairs: number) => (pairs > 0 ? t("cleanBody", { pairs }) : t("cleanEmpty")),
      unchecked: t("unchecked"),
      hiddenMark: t("hiddenMark"),
      showing: (shown: number, total: number) => t("showing", { shown, total }),
      filePairs: (count: number) => t("filePairs", { count }),
      coChanges: (count: number) => t("coChanges", { count }),
      lastTogether: (date: string) => t("lastTogether", { date }),
      unexplained: (count: number, pct: number) => t("unexplained", { count, pct }),
      allExplained: t("allExplained"),
      promptLabel: t("promptLabel"),
      bulkPromptLabel: (count: number) => t("bulkPromptLabel", { count }),
      promptTitle: t("promptTitle"),
      promptDescription: t("promptDescription"),
      backToAreas: t("backToAreas"),
      pairHeadline: (unexplained: number, pairs: number) =>
        t("pairHeadline", { unexplained, pairs }),
      allFilePairs: t("allFilePairs"),
      browseFilePairs: t("browseFilePairs"),
      workspaceLink: t("workspaceLink"),
      filters: {
        label: t("filters.label"),
        unexplained: t("filters.unexplained"),
        crossArea: t("filters.crossArea"),
        includeTests: t("filters.includeTests"),
        minSupport: t("filters.minSupport"),
        anySupport: t("filters.anySupport"),
        atLeast: (count: number) => t("filters.atLeast", { count }),
        recency: t("filters.recency"),
        anyTime: t("filters.anyTime"),
        lastDays: (days: number) => t(days >= 365 ? "filters.lastYear" : "filters.lastDays", { days }),
        depth: t("filters.depth"),
        autoDepth: (depth: number) => t("filters.autoDepth", { depth }),
      },
    }),
    [t],
  );
}
