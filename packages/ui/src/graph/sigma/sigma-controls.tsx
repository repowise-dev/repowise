"use client";

import { ZoomIn, ZoomOut, Maximize, Focus } from "lucide-react";
import { ActivityDot } from "../../ui/activity-dot";

interface SigmaControlsProps {
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFitView: () => void;
  onFocusSelected?: (() => void) | undefined;
  /** Drives the "Arranging…" activity chip only. There is no start/stop
   *  control: re-running FA2 measurably *worsens* this layout (cluster
   *  separation 23.2 seeded, 7.7 after 1200 iterations), and the golden-angle
   *  community seed is the layout. */
  isLayoutRunning: boolean;
}

export function SigmaControls({
  onZoomIn,
  onZoomOut,
  onFitView,
  onFocusSelected,
  isLayoutRunning,
}: SigmaControlsProps) {
  // Direct manipulation of the camera, so this is one of the few things that
  // stays on the drawing plane. Mobile-first sizing: 36px is a usable touch
  // target, compact 28px from `sm` up where a pointer is aiming.
  const btnClass =
    "flex h-9 w-9 sm:h-7 sm:w-7 items-center justify-center rounded-md text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-wash-hover)] hover:text-[var(--color-text-primary)]";

  return (
    // `--canvas-chrome-bottom` is published by `GraphCanvasShell` when a rail
    // is open as a bottom sheet, so these controls clear it instead of sitting
    // underneath. Falls back to the plain inset when nothing sets it.
    <div className="absolute bottom-[var(--canvas-chrome-bottom,0.75rem)] right-3 z-[var(--z-elevated)] flex flex-col items-end gap-1.5 transition-[bottom] duration-200 motion-reduce:transition-none">
      {isLayoutRunning && (
        <div className="flex items-center gap-1.5 whitespace-nowrap rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]/85 px-2 py-1 text-[10px] text-[var(--color-accent-primary)] shadow-sm backdrop-blur-sm">
          <ActivityDot className="h-1.5 w-1.5" />
          Arranging…
        </div>
      )}
      <div className="flex flex-col overflow-hidden rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]/85 p-1 shadow-sm backdrop-blur-sm">
        <button
          type="button"
          onClick={onZoomIn}
          className={btnClass}
          title="Zoom in"
          aria-label="Zoom in"
        >
          <ZoomIn className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={onZoomOut}
          className={btnClass}
          title="Zoom out"
          aria-label="Zoom out"
        >
          <ZoomOut className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={onFitView}
          className={btnClass}
          title="Fit view"
          aria-label="Fit view"
        >
          <Maximize className="h-3.5 w-3.5" />
        </button>
        {onFocusSelected && (
          <button
            type="button"
            onClick={onFocusSelected}
            className={btnClass}
            title="Focus selected"
            aria-label="Focus selected"
          >
            <Focus className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
    </div>
  );
}
