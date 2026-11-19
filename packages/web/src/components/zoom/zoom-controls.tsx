"use client";

/**
 * The map's own controls, in the canvas's bottom-left corner: zoom in, zoom
 * out, fit the whole system, up one level, then whatever the host slots in
 * after them (the map key, the first-visit hint). Each button calls the
 * canvas's imperative handle, the same command its keyboard shortcut runs, and
 * names that shortcut in its tooltip.
 */

import type { ReactNode, RefObject } from "react";
import { CornerLeftUp, Maximize, Minus, Plus } from "lucide-react";
import { KEY_ZOOM_FACTOR, type ZoomCanvasHandle } from "@repowise-dev/ui/zoom";
import { useTranslations } from "next-intl";

/** The frosted ground every on-canvas control shares. */
export const ZOOM_GLASS =
  "rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-glass)] shadow-sm backdrop-blur-sm";

export const ZOOM_ICON_BUTTON =
  "flex h-8 w-8 items-center justify-center text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-wash-hover)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--color-focus-ring)] disabled:opacity-40";

interface ZoomControlsProps {
  canvas: RefObject<ZoomCanvasHandle | null>;
  /** False at the overview, where "up" has nowhere to go. */
  canGoUp: boolean;
  children?: ReactNode;
}

export function ZoomControls({ canvas, canGoUp, children }: ZoomControlsProps) {
  const t = useTranslations("zoom.controls");
  const buttons = [
    { key: "in", label: t("zoomIn"), icon: Plus, act: () => canvas.current?.zoomBy(KEY_ZOOM_FACTOR) },
    { key: "out", label: t("zoomOut"), icon: Minus, act: () => canvas.current?.zoomBy(1 / KEY_ZOOM_FACTOR) },
    { key: "fit", label: t("fit"), icon: Maximize, act: () => canvas.current?.reset() },
    { key: "up", label: t("up"), icon: CornerLeftUp, act: () => canvas.current?.up(), disabled: !canGoUp },
  ];
  return (
    <div className="pointer-events-none absolute bottom-3 left-3 z-10 flex max-w-[calc(100%-1.5rem)] items-center gap-2">
      <div role="group" aria-label={t("label")} className={`pointer-events-auto flex shrink-0 overflow-hidden ${ZOOM_GLASS}`}>
        {buttons.map(({ key, label, icon: Icon, act, disabled }, i) => (
          <button
            key={key}
            type="button"
            aria-label={label}
            title={label}
            // A pointer click leaves focus on the canvas so its keys keep working.
            onMouseDown={(e) => e.preventDefault()}
            onClick={act}
            disabled={disabled}
            className={`${ZOOM_ICON_BUTTON} ${i > 0 ? "border-l border-[var(--color-border-default)]" : ""}`}
          >
            <Icon className="h-4 w-4" aria-hidden />
          </button>
        ))}
      </div>
      {children}
    </div>
  );
}
