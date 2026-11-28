"use client";

/**
 * The canvas frame while the zoom map is on its way, and when it failed. The
 * map takes several seconds to build on a large repository, so the wait shows
 * the shape of what is coming (the graph-paper ground, faint cards, the
 * controls corner) under one calm message, rather than a line of text in an
 * empty page. No progress bar: the server reports none, and a made-up one
 * would lie.
 */

import { useTranslations } from "next-intl";
import { ScanSearch } from "lucide-react";
import { Skeleton, SkeletonRegion } from "@repowise-dev/ui/ui/skeleton";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { ZOOM_GLASS } from "./zoom-controls";

/** The canvas frame; the ready canvas, the wait and the error share it so nothing jumps. */
export const ZOOM_FRAME =
  "relative min-h-[360px] flex-1 overflow-hidden rounded-lg border border-[var(--color-border-default)]";

/** The canvas's graph-paper ground (`GRID_SPACING_PX` = 32), drawn in CSS. */
const GRID_GROUND: React.CSSProperties = {
  backgroundColor: "var(--color-bg-root)",
  backgroundImage:
    "linear-gradient(to right, var(--color-zoom-grid) 1px, transparent 1px), linear-gradient(to bottom, var(--color-zoom-grid) 1px, transparent 1px)",
  backgroundSize: "32px 32px",
};

/** Placeholder card heights (px), a rough overview of a dozen layers. */
const CARDS = [112, 96, 104, 120, 100, 116, 92, 108, 98, 110, 90];

export function ZoomCanvasSkeleton() {
  const t = useTranslations("views.knowledgeGraph");
  return (
    <SkeletonRegion label={t("loading")} className={ZOOM_FRAME} style={GRID_GROUND}>
      <div
        aria-hidden
        className="absolute inset-x-[10%] top-1/2 grid -translate-y-1/2 grid-cols-3 gap-6 opacity-70 md:grid-cols-4"
      >
        {CARDS.map((h, i) => (
          <Skeleton key={i} className="rounded-lg border border-[var(--color-border-default)]" style={{ height: h }} />
        ))}
      </div>
      <div aria-hidden className="absolute inset-0 flex items-center justify-center p-4">
        <div className={`${ZOOM_GLASS} flex max-w-sm items-start gap-3 px-4 py-3`}>
          <ScanSearch className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-text-tertiary)]" />
          <div>
            <p className="text-sm font-medium text-[var(--color-text-primary)]">{t("loading")}</p>
            <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">{t("loadingHint")}</p>
          </div>
        </div>
      </div>
      <Skeleton aria-hidden className="absolute bottom-3 left-3 h-8 w-[10.5rem] rounded-lg" />
    </SkeletonRegion>
  );
}

export function ZoomCanvasError({ onRetry }: { onRetry: () => void }) {
  const t = useTranslations("views.knowledgeGraph");
  const tc = useTranslations("common");
  return (
    <div className={`${ZOOM_FRAME} flex items-center justify-center p-4`} style={GRID_GROUND}>
      <EmptyState
        tone="error"
        title={t("error")}
        description={t("errorHint")}
        action={{ label: tc("retry"), onClick: onRetry }}
        className="bg-[var(--color-bg-root)]"
      />
    </div>
  );
}
