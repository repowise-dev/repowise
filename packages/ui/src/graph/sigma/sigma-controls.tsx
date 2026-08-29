"use client";

import { ZoomIn, ZoomOut, Maximize, Focus, Play, Pause } from "lucide-react";
import { ActivityDot } from "../../ui/activity-dot";

interface SigmaControlsProps {
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFitView: () => void;
  onFocusSelected?: (() => void) | undefined;
  isLayoutRunning: boolean;
  onToggleLayout?: (() => void) | undefined;
}

export function SigmaControls({
  onZoomIn,
  onZoomOut,
  onFitView,
  onFocusSelected,
  isLayoutRunning,
  onToggleLayout,
}: SigmaControlsProps) {
  // One panel with hairline dividers, matching the toolbar and the legend, so
  // all three corners of the canvas read as the same system. Each button used
  // to be its own bordered, `shadow-lg` pill; five of them stacked down the
  // right edge was five frames and five shadows for one control.
  //
  // Dark mode also painted a hardcoded `#1a1a2e` navy that belongs to no
  // palette in this app — the same class of drift as `--color-bg-glass`, and
  // it survived for the same reason: it only ever renders on top of a diagram,
  // where a plane one step off reads as deliberate.
  // Mobile-first sizing, matching graph-toolbar's buttons: 36px is a usable
  // touch target on a phone, and the compact 28px returns from `sm` up where a
  // pointer is doing the aiming.
  const btnClass =
    "flex h-9 w-9 sm:h-7 sm:w-7 items-center justify-center rounded-md text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-wash-hover)] hover:text-[var(--color-text-primary)]";

  return (
    <div className="absolute bottom-3 right-3 z-10 flex flex-col items-end gap-1.5">
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
        {onToggleLayout && (
          <button
            type="button"
            onClick={onToggleLayout}
            className={`${btnClass} mt-1 border-t border-[var(--color-border-default)] pt-1 ${
              isLayoutRunning ? "text-[var(--color-accent-primary)]" : ""
            }`}
            title={isLayoutRunning ? "Stop arranging" : "Re-arrange nodes"}
            aria-label={isLayoutRunning ? "Stop arranging" : "Re-arrange nodes"}
            aria-pressed={isLayoutRunning}
          >
            {isLayoutRunning ? (
              <Pause className="h-3.5 w-3.5" />
            ) : (
              <Play className="h-3.5 w-3.5" />
            )}
          </button>
        )}
      </div>
    </div>
  );
}
