"use client";

/**
 * The lens switcher and the key, as components a host places around the map
 * rather than on top of it.
 *
 * Both read the same lens specs the canvas does, so an off-canvas key can
 * never drift from the marks it is describing.
 */

// Aliased: the bare name would shadow the DOM `KeyboardEvent`.
import { Fragment, useState } from "react";
import { ChevronDown } from "lucide-react";
import type { LegendRow, MapLens } from "./lens";
import { OVERLAY_ORDER, OVERLAY_SPECS } from "./lens-registry";
import type { CodeHealthOverlay } from "./types";
import { ActivityDot } from "../../ui/activity-dot";
import { Popover, PopoverContent, PopoverTrigger } from "../../ui/popover";
import { Segmented } from "../../shared/segmented";

/** One key swatch: a disc in the row's fill, or a pill for a run of the ramp. */
function Swatch({ row, size }: { row: LegendRow; size: number }) {
  const run = row.fill.startsWith("linear-gradient");
  return (
    <span
      aria-hidden
      className="inline-block shrink-0 rounded-full"
      style={{ width: run ? size * 2.4 : size, height: size, background: row.fill }}
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
export function MapLegendRows({ spec, loading }: { spec: MapLens; loading: boolean }) {
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
            <span className="mt-1 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)] first:mt-0">
              {row.group}
            </span>
          ) : null}
          <span className="flex items-center gap-1.5 text-[var(--color-text-tertiary)]">
            <Swatch row={row} size={10} />
            {row.label}
          </span>
        </Fragment>
      ))}
      <span className="mt-1 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        Size
      </span>
      <SizeKey className="text-[var(--color-text-tertiary)]" />
    </div>
  );
}

/**
 * The lens switcher: the shared segmented control for the primary lenses, and
 * the rest behind a "More" menu. A lens picked from the menu joins the row as
 * the selected segment, so the active lens is always on screen. Without
 * `primary`, every lens is a segment, as before.
 *
 * Future lenses (off-boarding, coupling) join `lenses` and land in the menu.
 */
export function MapLensSwitcher({
  overlay,
  onOverlayChange,
  lenses = OVERLAY_ORDER,
  primary,
  moreLabel = "More",
  nameOf,
  className,
}: {
  overlay: CodeHealthOverlay;
  onOverlayChange: (overlay: CodeHealthOverlay) => void;
  lenses?: CodeHealthOverlay[];
  /** Lenses shown as segments; the others go behind "More". */
  primary?: CodeHealthOverlay[];
  moreLabel?: string;
  /** A lens's display name, for a host that translates them. */
  nameOf?: (lens: CodeHealthOverlay) => string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const name = (lens: CodeHealthOverlay) => nameOf?.(lens) ?? OVERLAY_SPECS[lens].label;
  const main = primary ? lenses.filter((l) => primary.includes(l)) : lenses;
  const rest = primary ? lenses.filter((l) => !primary.includes(l)) : [];
  const shown = rest.includes(overlay) ? [...main, overlay] : main;
  return (
    <div className={`flex min-w-0 flex-wrap items-start gap-1.5 ${className ?? ""}`}>
      <Segmented
        label="Map lens"
        value={overlay}
        options={shown.map((value) => ({ value, label: name(value) }))}
        onChange={onOverlayChange}
      />
      {rest.length > 0 ? (
        <Popover open={open} onOpenChange={setOpen}>
          <PopoverTrigger asChild>
            <button
              type="button"
              aria-haspopup="menu"
              className="inline-flex items-center gap-1 rounded-md border border-[var(--color-border-default)] px-2.5 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
            >
              {moreLabel}
              <ChevronDown aria-hidden className="h-3.5 w-3.5" />
            </button>
          </PopoverTrigger>
          <PopoverContent role="menu" align="end" className="w-48 p-1">
            {rest.map((lens) => (
              <button
                key={lens}
                type="button"
                role="menuitemradio"
                aria-checked={lens === overlay}
                onClick={() => {
                  setOpen(false);
                  onOverlayChange(lens);
                }}
                className={`flex w-full items-center rounded px-2 py-1.5 text-left text-xs hover:bg-[var(--color-bg-elevated)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] ${
                  lens === overlay
                    ? "font-semibold text-[var(--color-text-primary)]"
                    : "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
                }`}
              >
                {name(lens)}
              </button>
            ))}
          </PopoverContent>
        </Popover>
      ) : null}
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
  lens,
  loading = false,
  className,
}: {
  overlay: CodeHealthOverlay;
  /** A resolved or plug-in lens; wins over `overlay`. */
  lens?: MapLens;
  loading?: boolean;
  className?: string;
}) {
  const spec = lens ?? OVERLAY_SPECS[overlay] ?? OVERLAY_SPECS.health;
  return (
    <div
      className={`flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-[var(--color-text-tertiary)] ${className ?? ""}`}
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
              <span className="ml-1 font-mono text-[10px] uppercase tracking-[0.12em] first:ml-0">
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
