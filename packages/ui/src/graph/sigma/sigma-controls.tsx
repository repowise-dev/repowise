"use client";

import { ZoomIn, ZoomOut, Maximize, Focus, Play, Pause } from "lucide-react";
import { Button } from "../../ui/button";

interface SigmaControlsProps {
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFitView: () => void;
  onFocusSelected?: (() => void) | undefined;
  isLayoutRunning: boolean;
  onToggleLayout?: (() => void) | undefined;
  graphTheme: "light" | "dark";
}

export function SigmaControls({
  onZoomIn,
  onZoomOut,
  onFitView,
  onFocusSelected,
  isLayoutRunning,
  onToggleLayout,
  graphTheme,
}: SigmaControlsProps) {
  const isDark = graphTheme === "dark";
  const btnClass = isDark
    ? "h-7 w-7 p-0 border-white/10 bg-[#1a1a2e] text-white/60 hover:bg-[#252540] hover:text-white shadow-lg shadow-black/40"
    : "h-7 w-7 p-0 border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-overlay)] hover:text-[var(--color-text-primary)] shadow-lg shadow-black/20";

  return (
    <div className="absolute bottom-3 right-3 z-10 flex flex-col gap-1">
      <Button
        size="sm"
        variant="outline"
        onClick={onZoomIn}
        className={btnClass}
        title="Zoom in"
        aria-label="Zoom in"
      >
        <ZoomIn className="w-3.5 h-3.5" />
      </Button>
      <Button
        size="sm"
        variant="outline"
        onClick={onZoomOut}
        className={btnClass}
        title="Zoom out"
        aria-label="Zoom out"
      >
        <ZoomOut className="w-3.5 h-3.5" />
      </Button>
      <Button
        size="sm"
        variant="outline"
        onClick={onFitView}
        className={btnClass}
        title="Fit view"
        aria-label="Fit view"
      >
        <Maximize className="w-3.5 h-3.5" />
      </Button>
      {onFocusSelected && (
        <Button
          size="sm"
          variant="outline"
          onClick={onFocusSelected}
          className={btnClass}
          title="Focus selected"
          aria-label="Focus selected"
        >
          <Focus className="w-3.5 h-3.5" />
        </Button>
      )}
      {onToggleLayout && (
        <Button
          size="sm"
          variant="outline"
          onClick={onToggleLayout}
          className={btnClass}
          title={isLayoutRunning ? "Stop layout" : "Run layout"}
          aria-label={isLayoutRunning ? "Stop layout" : "Run layout"}
        >
          {isLayoutRunning ? (
            <Pause className="w-3.5 h-3.5" />
          ) : (
            <Play className="w-3.5 h-3.5" />
          )}
        </Button>
      )}
      {isLayoutRunning && (
        <div className="text-[10px] text-center text-[var(--color-accent-graph)] animate-pulse whitespace-nowrap">
          Layout optimizing...
        </div>
      )}
    </div>
  );
}
