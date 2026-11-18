"use client";

import { useMemo } from "react";
import { useTranslations } from "next-intl";
import type { ZoomCanvasLabels } from "@repowise-dev/ui/zoom";

/** The canvas's accessible names and hover-card copy, translated; stable per locale. */
export function useZoomCanvasLabels(): ZoomCanvasLabels {
  const t = useTranslations("zoom.canvas");
  return useMemo(
    () => ({
      canvas: t("aria"),
      minimap: t("minimap"),
      relationCount: (count: number) => t("relationCount", { count }),
      capOnMap: (shown: number) => t("capOnMap", { shown }),
      containerSpan: (count: number) => t("containerSpan", { count }),
      externalUsage: (imports: number, files: number) => t("externalUsage", { imports, files }),
      externalMostFrom: t("externalMostFrom"),
      externalDive: (name: string) => t("externalDive", { name }),
    }),
    [t],
  );
}
