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
 * Renders nothing theme-dependent until mounted so the control doesn't flash
 * the wrong selection during hydration (next-themes returns `undefined` on
 * the server).
 */

import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { Sun, Moon } from "lucide-react";
import { cn } from "../lib/cn";

const OPTIONS = [
  { value: "light" as const, label: "Light", icon: Sun },
  { value: "dark" as const, label: "Dark", icon: Moon },
];

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
      className={cn(
        "inline-flex items-center gap-1 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-1",
        className,
      )}
    >
      {OPTIONS.map((opt) => {
        const Icon = opt.icon;
        const selected = mounted && theme === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={selected}
            aria-label={opt.label}
            title={opt.label}
            onClick={() => setTheme(opt.value)}
            className={cn(
              "inline-flex items-center justify-center gap-1.5 rounded-md text-xs font-medium transition-colors",
              compact ? "px-2 py-1.5" : "px-3 py-1.5",
              selected
                ? "bg-[var(--color-bg-surface)] text-[var(--color-text-primary)] shadow-[var(--shadow-sm)]"
                : "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]",
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
