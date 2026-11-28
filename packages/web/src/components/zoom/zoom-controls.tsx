"use client";

/**
 * The shared zoom controls with this app's translated labels. The component
 * and its frosted-glass classes live in the shared UI package so the editor
 * extension's Knowledge Graph panel draws the same corner.
 */

import type { ReactNode, RefObject } from "react";
import type { ZoomCanvasHandle } from "@repowise-dev/ui/zoom";
import { ZoomControls as SharedZoomControls } from "@repowise-dev/ui/zoom/zoom-controls";
import { useTranslations } from "next-intl";

export { ZOOM_GLASS, ZOOM_ICON_BUTTON } from "@repowise-dev/ui/zoom/zoom-controls";

interface ZoomControlsProps {
  canvas: RefObject<ZoomCanvasHandle | null>;
  /** False at the overview, where "up" has nowhere to go. */
  canGoUp: boolean;
  children?: ReactNode;
}

export function ZoomControls({ canvas, canGoUp, children }: ZoomControlsProps) {
  const t = useTranslations("zoom.controls");
  return (
    <SharedZoomControls
      canvas={canvas}
      canGoUp={canGoUp}
      labels={{
        label: t("label"),
        zoomIn: t("zoomIn"),
        zoomOut: t("zoomOut"),
        fit: t("fit"),
        up: t("up"),
      }}
    >
      {children}
    </SharedZoomControls>
  );
}
