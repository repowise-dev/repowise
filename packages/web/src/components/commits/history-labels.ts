"use client";

import { useMemo } from "react";
import { useTranslations } from "next-intl";
import type { HealthDeltaLabels } from "@repowise-dev/ui/commits/health-delta-mark";
import type { HistoryFilterLabels } from "@repowise-dev/ui/commits/history-filters";

/** The health chip's words, translated; one object per locale render. */
export function useHealthDeltaLabels(): HealthDeltaLabels {
  const t = useTranslations("views.commits.health");
  return useMemo(
    () => ({
      improved: t("improved"),
      worsened: t("worsened"),
      mixed: t("mixed"),
      neutral: t("neutral"),
      notCompared: t("notAnalysed"),
      partial: t("partial"),
      stale: t("stale"),
    }),
    [t],
  );
}

/** The quick filters' words, translated. */
export function useHistoryFilterLabels(): HistoryFilterLabels {
  const t = useTranslations("views.commits.filters");
  return useMemo(
    () => ({
      impactLabel: t("impactLabel"),
      all: t("all"),
      better: t("better"),
      worse: t("worse"),
      none: t("none"),
      hotspots: t("hotspots"),
      windowLabel: t("windowLabel"),
      week: t("week"),
      month: t("month"),
      quarter: t("quarter"),
      allTime: t("allTime"),
      custom: t("custom"),
      from: t("from"),
      to: t("to"),
      author: t("author"),
      area: t("area"),
    }),
    [t],
  );
}
