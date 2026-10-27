"use client";

import { useMemo } from "react";
import { useTranslations } from "next-intl";
import type { AreaRailLabels } from "@repowise-dev/ui/health";

/** The area rail's copy from the catalog. */
export function useAreaRailLabels(): AreaRailLabels {
  const t = useTranslations("views.codeHealth.areaRail");
  return useMemo<AreaRailLabels>(
    () => ({
      mapTitle: t("mapTitle"),
      mapDescription: t("mapDescription"),
      flatMapDescription: t("flatMapDescription"),
      searchPlaceholder: t("searchPlaceholder"),
      moreLenses: t("moreLenses"),
      scopeProduction: t("scopeProduction"),
      scopeAll: t("scopeAll"),
      heldOutHint: t("heldOutHint"),
      scopeWith: (names) => t("scopeWith", { names }),
      originName: (origin) => (t.has(`origin.${origin}`) ? t(`origin.${origin}`) : origin.replace(/_/g, " ")),
      showHeld: (name, count) => t("showHeld", { name, count: count.toLocaleString() }),
      hideHeld: (name) => t("hideHeld", { name }),
      hotspotChip: (count, health) =>
        health == null ? t("hotspotChip", { count }) : t("hotspotChipScored", { count, health }),
      fileCount: (count) => t("fileCount", { count }),
      openHint: t("openHint"),
      directFiles: (folder) => t("directFiles", { folder }),
      notLoaded: t("notLoaded"),
      analysedFiles: (analysed, loaded) => t("analysedFiles", { analysed, loaded }),
      flaggedFiles: (flagged, analysed) => t("flaggedFiles", { flagged, analysed }),
      lensName: (lens) => (t.has(`lens.${lens}`) ? t(`lens.${lens}`) : undefined),
      mapLoadError: t("mapLoadError"),
      mapAriaLabel: (lens) => t("mapAriaLabel", { lens }),
      promptAction: (count) => t("promptAction", { count }),
      repository: t("repository"),
      repoEyebrow: t("repoEyebrow"),
      areaEyebrow: t("areaEyebrow"),
      files: (count) => t("files", { count }),
      deltaWindow: (delta, days) => t("deltaWindow", { delta, days }),
      improved: (delta, days) => t("improved", { delta, days }),
      noDelta: t("noDelta"),
      needsAttention: t("needsAttention"),
      subAreas: t("subAreas"),
      improvedMost: t("improvedMost"),
      worstFiles: t("worstFiles"),
      hotspots: (count) => t("hotspots", { count }),
      owners: t("owners"),
      ownerShare: (name, pct) => t("ownerShare", { name, pct }),
      busFactorOne: (count) => t("busFactorOne", { count }),
      coverage: t("coverage"),
      noCoverage: t("noCoverage"),
      coverageLine: (pct, files) => t("coverageLine", { pct, files }),
      topFindings: t("topFindings"),
      noFindings: t("noFindings"),
      allClean: (count) => t("allClean", { count }),
      promptTitle: t("promptTitle"),
      promptDescription: t("promptDescription"),
      findingsLink: t("findingsLink"),
      fixFirstLink: t("fixFirstLink"),
      contributorsLink: t("contributorsLink"),
      backToArea: (name) => t("backToArea", { name }),
      loading: t("loading"),
      loadError: t("loadError"),
    }),
    [t],
  );
}
