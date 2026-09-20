"use client";

/**
 * ThemeToggle — shared segmented Light / Dark control.
 *
 * Canonical implementation consumed by both `packages/web` and the hosted
 * frontend so the toggle UX stays identical across surfaces. Relies on
 * `next-themes` (a peer dependency of consumers) for state + persistence;
 * this component just calls `setTheme()`.
 *
 * Deliberately two-state — no "System" option (product decision: keep the
 * choice explicit). Consumers set `enableSystem={false}` on their provider;
 * the mount effect below migrates any stale persisted "system" value to the
 * light default so pre-simplification visitors don't keep an unknown theme.
 * The migration only fires for non light/dark values, so an explicit Light or
 * Dark choice is never rewritten.
 *
 * The selected option is expressed in CSS off the `dark` class that
 * next-themes writes on `<html>` before first paint, NOT off React state.
 * `useTheme()` returns `undefined` on the server, so a state-driven control
 * renders one frame with neither option selected. Reading the class instead
 * means the first painted frame is already correct. `aria-checked` still
 * waits for mount, because markup cannot be derived from an ancestor class.
 */

import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { Sun, Moon } from "lucide-react";
import { cn } from "../lib/cn";

const OPTIONS = [
  { value: "light" as const, label: "Light", icon: Sun },
  { value: "dark" as const, label: "Dark", icon: Moon },
];

const SELECTED =
  "bg-[var(--color-bg-surface)] text-[var(--color-text-primary)] shadow-[var(--shadow-sm)]";
const UNSELECTED =
  "bg-transparent text-[var(--color-text-secondary)] shadow-none hover:text-[var(--color-text-primary)]";
const DARK_SELECTED =
  "dark:bg-[var(--color-bg-surface)] dark:text-[var(--color-text-primary)] dark:shadow-[var(--shadow-sm)]";
const DARK_UNSELECTED =
  "dark:bg-transparent dark:text-[var(--color-text-secondary)] dark:shadow-none dark:hover:text-[var(--color-text-primary)]";

export interface ThemeToggleProps {
  /** Hide the text labels and render an icon-only compact control. */
  compact?: boolean;
  className?: string;
}

export function ThemeToggle({ compact = false, className }: ThemeToggleProps) {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  // Migrate a persisted "system" (or any unknown) theme from before the
  // simplification to the explicit light default. Only fires for non
  // light/dark values, so an explicit user choice is never clobbered.
  useEffect(() => {
    if (mounted && theme !== "light" && theme !== "dark") setTheme("light");
  }, [mounted, theme, setTheme]);

  return (
    <div
      role="radiogroup"
      aria-label="Theme preference"
      // No resting border, fill, or shadow: this is a once-per-session
      // control and the permanent track was carrying more weight than the
      // navigation above it. The selected pill is the only ground.
      className={cn("inline-flex items-center gap-1 rounded-lg p-0.5", className)}
    >
      {OPTIONS.map((opt) => {
        const Icon = opt.icon;
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={mounted && theme === opt.value}
            aria-label={opt.label}
            title={opt.label}
            onClick={() => setTheme(opt.value)}
            className={cn(
              "inline-flex items-center justify-center gap-1.5 rounded-md text-xs font-medium transition-colors",
              compact ? "px-1.5 py-1" : "px-3 py-1.5",
              // Selected-ness comes from the ancestor `dark` class, so it is
              // right on the first painted frame. Each option is styled
              // selected in its own theme and quiet in the other.
              opt.value === "light"
                ? [SELECTED, DARK_UNSELECTED]
                : [UNSELECTED, DARK_SELECTED],
            )}
          >
            <Icon className="h-3.5 w-3.5 shrink-0" />
            {!compact && opt.label}
          </button>
        );
      })}
    </div>
  );
}
