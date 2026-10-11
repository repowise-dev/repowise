"use client";

import { useTheme } from "next-themes";
import { ThinkingOrb, type OrbSize, type OrbState } from "thinking-orbs";
import { cn } from "../lib/cn";

export type { OrbSize, OrbState };

/**
 * One orb animation per kind of work, so the same motion always means the
 * same thing across the product. Pick by what the wait is doing:
 *
 * - `thinking`  breathing  - model reasoning before any output
 * - `searching` searching  - search / index lookups
 * - `tracing`   connecting - graph, call-path and architecture layout
 * - `working`   working    - generic tool or job work (inline default)
 * - `writing`   composing  - generating text or pages (region default)
 * - `analysing` solving    - health, risk and other analysis passes
 * - `workspace` weaving    - cross-repo / workspace work
 * - `page`      shaping    - a whole page or panel assembling
 */
export const ORB_STATE = {
  thinking: "breathing",
  searching: "searching",
  tracing: "connecting",
  working: "working",
  writing: "composing",
  analysing: "solving",
  workspace: "weaving",
  page: "shaping",
} as const satisfies Record<string, OrbState>;

export type OrbIntent = keyof typeof ORB_STATE;

export interface OrbLoaderProps {
  /**
   * The animation, ideally from `ORB_STATE` (e.g. `ORB_STATE.tracing`).
   * Defaults to `composing` at 64px and `working` at 20/32px.
   */
  state?: OrbState;
  /**
   * 64 for a region or canvas, 20 for inline next to text. 32 is an
   * interpolated in-between for tight panels. Default 64.
   */
  size?: OrbSize;
  /** Accessible name, and the visible caption at 64px. Default "Loading". */
  label?: string;
  /** Show `label` under the orb. Defaults to true at 64px only. */
  showLabel?: boolean;
  /**
   * Centre the orb in all the space its parent gives it. Off by default;
   * the loader never invents a height of its own.
   */
  fill?: boolean;
  className?: string;
}

/**
 * The loader for waits with no known shape (canvases, graphs, streaming).
 * Known shapes use `PageSkeleton` / `SkeletonRegion`; buttons use `Spinner`.
 *
 * A monochrome dotted orb on a 2D canvas: no network, no WASM, pauses
 * offscreen, and shows a still frame under reduced motion. The canvas paints
 * client-side only, at a fixed size, so SSR reserves the box without a shift.
 * The theme comes from the app's resolved theme rather than the library's
 * own detection, which falls back to the OS setting when no ancestor carries
 * a `dark`/`light` class (the VS Code webview's light theme).
 */
export function OrbLoader({
  state,
  size = 64,
  label = "Loading",
  showLabel,
  fill = false,
  className,
}: OrbLoaderProps) {
  const { resolvedTheme } = useTheme();
  const theme = resolvedTheme === "dark" || resolvedTheme === "light" ? resolvedTheme : "auto";
  const region = size === 64;
  const caption = showLabel ?? region;

  const orb = (
    <ThinkingOrb
      state={state ?? (region ? ORB_STATE.writing : ORB_STATE.working)}
      size={size}
      theme={theme}
      aria-hidden="true"
      className="shrink-0"
    />
  );

  if (!region) {
    return (
      <span
        role="status"
        aria-label={label}
        data-orb-loader=""
        className={cn(
          "inline-flex items-center gap-2 text-xs text-[var(--color-text-tertiary)]",
          fill && "h-full w-full justify-center",
          className,
        )}
      >
        {orb}
        {caption && <span>{label}</span>}
      </span>
    );
  }

  return (
    <div
      role="status"
      aria-label={label}
      data-orb-loader=""
      className={cn(
        "flex flex-col items-center justify-center gap-3",
        fill && "h-full min-h-0 w-full flex-1",
        className,
      )}
    >
      {orb}
      {caption && <span className="text-xs text-[var(--color-text-tertiary)]">{label}</span>}
    </div>
  );
}
