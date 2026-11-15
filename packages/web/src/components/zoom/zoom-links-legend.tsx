"use client";

/**
 * The map key's line about the arrows drawn at rest: the level the camera
 * looks into shows only its strongest few links, and a map that bounds what it
 * draws has to say so, or the partial view reads as the whole one.
 */

import { useMemo } from "react";
import { restLinkSummary, type ZoomMap } from "@repowise-dev/ui/zoom";
import { useTranslations } from "next-intl";

interface ZoomLinksLegendProps {
  map: ZoomMap;
  /** The node the camera looks into, whose children's links draw at rest. */
  levelId: string;
  relationVerbs: ReadonlySet<string> | null;
  showTestLinks?: boolean;
}

export function ZoomLinksLegend({ map, levelId, relationVerbs, showTestLinks = false }: ZoomLinksLegendProps) {
  const t = useTranslations("zoom");
  const { shown, total } = useMemo(
    () => restLinkSummary(map, levelId, { relationVerbs, showTestLinks }),
    [map, levelId, relationVerbs, showTestLinks],
  );
  if (total === 0) return null;
  return (
    <p className="border-t border-[var(--color-border-default)] pt-3 text-[12px] text-[var(--color-text-tertiary)] tabular-nums">
      <span className="text-[var(--color-text-secondary)]">{t("mapKey.linksShown")}</span>{" "}
      {shown < total
        ? t("mapKey.linksShownSome", { shown, total })
        : t("mapKey.linksShownAll", { total })}
    </p>
  );
}
