"use client";

import { useMemo } from "react";
import { useTranslations } from "next-intl";
import { PreviewBadgeStringsContext } from "@repowise-dev/ui/health/preview-badge";

/** Hands the shared Preview badge its translated copy, once for every view. */
export function PreviewBadgeStrings({ children }: { children: React.ReactNode }) {
  const t = useTranslations("common.preview");
  const strings = useMemo(
    () => ({ label: t("label"), ariaLabel: (note: string) => t("ariaLabel", { note }) }),
    [t],
  );
  return (
    <PreviewBadgeStringsContext.Provider value={strings}>{children}</PreviewBadgeStringsContext.Provider>
  );
}
