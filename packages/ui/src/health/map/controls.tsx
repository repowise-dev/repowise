"use client";

/**
 * The few controls that sit on the canvas: zoom and fit, the way out of a
 * focused galaxy, and, for a host with nowhere else to put them, the lens
 * switcher and key.
 */

import { MapLegendRows, MapLensSwitcher } from "./legend";
import type { OverlaySpec } from "./lens";
import type { CodeHealthOverlay } from "./types";

const GLASS =
  "rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-glass)] shadow-sm backdrop-blur-sm";

export function ZoomControls({
  onZoomIn,
  onZoomOut,
  onFit,
}: {
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFit: () => void;
}) {
  const buttons = [
    { label: "Zoom in", text: "+", act: onZoomIn },
    { label: "Zoom out", text: "−", act: onZoomOut },
    { label: "Fit the whole field", text: "Fit", act: onFit },
  ];
  return (
    <div role="group" aria-label="Zoom" className={`flex overflow-hidden text-xs ${GLASS}`}>
      {buttons.map((b, i) => (
        <button
          key={b.label}
          type="button"
          aria-label={b.label}
          title={b.label}
          onClick={b.act}
          className={`min-w-[28px] px-2 py-1 font-mono tabular-nums text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--color-accent-primary)] ${
            i > 0 ? "border-l border-[var(--color-border-default)]" : ""
          }`}
        >
          {b.text}
        </button>
      ))}
    </div>
  );
}

/** The focused galaxy's name and the way back to the whole field. */
export function FocusCrumb({ label, onLeave }: { label: string; onLeave: () => void }) {
  return (
    <div className={`flex items-center gap-1.5 px-2.5 py-1.5 text-xs ${GLASS}`}>
      <span className="max-w-[180px] truncate font-medium text-[var(--color-text-primary)]">
        {label}
      </span>
      <button
        type="button"
        onClick={onLeave}
        className="ml-1 rounded border border-[var(--color-border-default)] px-1.5 py-0.5 text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
      >
        ← Overview
      </button>
    </div>
  );
}

/** The lens switcher and key floated over the canvas (`chrome="canvas"`). */
export function CanvasChrome({
  overlay,
  spec,
  lenses,
  loading,
  onOverlayChange,
}: {
  overlay: CodeHealthOverlay;
  spec: OverlaySpec;
  lenses: CodeHealthOverlay[];
  loading: boolean;
  onOverlayChange?: ((overlay: CodeHealthOverlay) => void) | undefined;
}) {
  return (
    <>
      {onOverlayChange ? (
        <div className={`absolute left-3 top-3 p-1 ${GLASS}`}>
          <MapLensSwitcher
            overlay={overlay}
            onOverlayChange={onOverlayChange}
            lenses={lenses}
            className="border-0"
          />
        </div>
      ) : null}
      <div
        className={`absolute left-3 max-w-[220px] px-3 py-2 text-xs ${GLASS} ${
          onOverlayChange ? "top-14" : "top-3"
        }`}
      >
        <div className="mb-1.5 font-medium text-[var(--color-text-secondary)]">{spec.label}</div>
        <MapLegendRows spec={spec} loading={loading} />
        <div className="mt-2 border-t border-[var(--color-border-default)] pt-1.5 text-[var(--color-text-tertiary)]">
          {spec.caption}
          <br />
          click a galaxy to zoom
        </div>
      </div>
    </>
  );
}
