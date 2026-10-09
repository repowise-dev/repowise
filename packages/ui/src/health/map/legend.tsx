"use client";

/**
 * The lens switcher and the key, as components a host places around the map
 * rather than on top of it.
 *
 * Both read the same lens specs the canvas does, so an off-canvas key can
 * never drift from the marks it is describing.
 */

// Aliased: the bare name would shadow the DOM `KeyboardEvent`.
import { Fragment } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { OVERLAY_ORDER, OVERLAY_SPECS, type LegendRow, type OverlaySpec } from "./lens";
import type { CodeHealthOverlay } from "./types";
import { ActivityDot } from "../../ui/activity-dot";

/** One key swatch: a disc in the row's fill. */
function Swatch({ row, size }: { row: LegendRow; size: number }) {
  return (
    <span
      aria-hidden
      className="inline-block shrink-0 rounded-full"
      style={{ width: size, height: size, backgroundColor: row.fill }}
    />
  );
}

/**
 * The one channel every lens shares, keyed once.
 *
 * A node's radius is its line count under all of them, and until now that was
 * stated only in prose in a caption. Two discs and a label is what a reader
 * can actually match against the field.
 */
function SizeKey({ className }: { className?: string }) {
  return (
    <span className={`flex items-center gap-1.5 ${className ?? ""}`}>
      <span aria-hidden className="flex items-end gap-0.5">
        <span className="inline-block h-1 w-1 rounded-full bg-[var(--color-text-tertiary)]" />
        <span className="inline-block h-2.5 w-2.5 rounded-full bg-[var(--color-text-tertiary)]" />
      </span>
      lines of code
    </span>
  );
}

/** The active lens's key rows, shared by both placements. */
export function MapLegendRows({ spec, loading }: { spec: OverlaySpec; loading: boolean }) {
  if (loading) {
    return (
      <div className="flex items-center gap-1.5 text-[var(--color-text-tertiary)]">
        <ActivityDot className="h-2.5 w-2.5 bg-[var(--color-text-tertiary)]" />
        loading {spec.label.toLowerCase()}…
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-1">
      {spec.legend.map((row, i) => (
        <Fragment key={row.label}>
          {row.group && row.group !== spec.legend[i - 1]?.group ? (
            <span className="mt-1 font-mono text-[9px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)] first:mt-0">
              {row.group}
            </span>
          ) : null}
          <span className="flex items-center gap-1.5 text-[var(--color-text-tertiary)]">
            <Swatch row={row} size={10} />
            {row.label}
          </span>
        </Fragment>
      ))}
      <span className="mt-1 font-mono text-[9px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        Size
      </span>
      <SizeKey className="text-[var(--color-text-tertiary)]" />
    </div>
  );
}

/**
 * The lens switcher as a real segmented control.
 *
 * A radiogroup rather than a row of pressed buttons: picking a lens is one
 * choice out of a set, and the roving-tabindex arrow-key behaviour comes free
 * with the role.
 */
export function MapLensSwitcher({
  overlay,
  onOverlayChange,
  lenses = OVERLAY_ORDER,
  className,
}: {
  overlay: CodeHealthOverlay;
  onOverlayChange: (overlay: CodeHealthOverlay) => void;
  lenses?: CodeHealthOverlay[];
  className?: string;
}) {
  const onKeyDown = (e: ReactKeyboardEvent) => {
    const i = lenses.indexOf(overlay);
    if (i < 0) return;
    let next = i;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (i + 1) % lenses.length;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp")
      next = (i - 1 + lenses.length) % lenses.length;
    else return;
    e.preventDefault();
    const target = lenses[next];
    if (target) onOverlayChange(target);
  };

  return (
    <div
      role="radiogroup"
      aria-label="Map lens"
      onKeyDown={onKeyDown}
      className={`inline-flex rounded-md border border-[var(--color-border-default)] p-0.5 ${className ?? ""}`}
    >
      {lenses.map((mode) => {
        const active = overlay === mode;
        return (
          <button
            key={mode}
            type="button"
            role="radio"
            aria-checked={active}
            tabIndex={active ? 0 : -1}
            onClick={() => onOverlayChange(mode)}
            className={`rounded px-2.5 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] ${
              active
                ? "bg-[var(--color-bg-elevated)] font-semibold text-[var(--color-text-primary)]"
                : "font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
            }`}
          >
            {OVERLAY_SPECS[mode].label}
          </button>
        );
      })}
    </div>
  );
}

/**
 * The active lens's key, laid out as a horizontal strip to sit under the map.
 *
 * Same content as the on-canvas panel, arranged to read as a caption rather
 * than as a floating card.
 */
export function MapLegend({
  overlay,
  loading = false,
  className,
}: {
  overlay: CodeHealthOverlay;
  loading?: boolean;
  className?: string;
}) {
  const spec = OVERLAY_SPECS[overlay] ?? OVERLAY_SPECS.health;
  return (
    <div
      className={`flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] text-[var(--color-text-tertiary)] ${className ?? ""}`}
    >
      {loading ? (
        <span className="flex items-center gap-1.5">
          <ActivityDot className="bg-[var(--color-text-tertiary)]" />
          loading {spec.label.toLowerCase()}…
        </span>
      ) : (
        spec.legend.map((row, i) => (
          <Fragment key={row.label}>
            {row.group && row.group !== spec.legend[i - 1]?.group ? (
              <span className="ml-1 font-mono text-[9px] uppercase tracking-[0.12em] first:ml-0">
                {row.group}
              </span>
            ) : null}
            <span className="flex items-center gap-1.5">
              <Swatch row={row} size={8} />
              {row.label}
            </span>
          </Fragment>
        ))
      )}
      {loading ? null : <SizeKey />}
      <span className="font-mono text-[10px] uppercase tracking-[0.12em]">{spec.caption}</span>
    </div>
  );
}
