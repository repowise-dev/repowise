"use client";

import { useMemo } from "react";
import { useTranslations } from "next-intl";
import type { HealthDeltaLabels } from "@repowise-dev/ui/commits/health-delta-mark";
import type { HealthDeltaLedeCopy } from "@repowise-dev/ui/commits/health-delta-lede";
import type { HistoryFilterLabels } from "@repowise-dev/ui/commits/history-filters";
import { healthDeltaLabels } from "@/lib/changes";

/** The health mark's words: the same ones the change records use. */
export function useHealthDeltaLabels(): HealthDeltaLabels {
  const t = useTranslations("views.changes");
  return useMemo(() => healthDeltaLabels(t), [t]);
}

/** The commit sheet's lede, worded for one commit. */
export function useCommitLedeCopy(): HealthDeltaLedeCopy {
  const t = useTranslations("views.commits.lede");
  return useMemo(
    () => ({
      label: t("label"),
      unitWorse: (worse, better) => t("unitWorse", { worse, better }),
      unitBetter: (better) => t("unitBetter", { better }),
      unitNeutral: t("unitNeutral"),
      worse: t("worse"),
      improved: (better) => t("improved", { better }),
      neutral: t("neutral"),
      stale: t("stale"),
      unknown: t("unknown"),
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
      moreFilters: t("moreFilters"),
      clearAll: t("clearAll"),
      impactPending: t("impactPending"),
    }),
    [t],
  );
}
