"use client";

/**
 * ThemeToggle — shared segmented System / Light / Dark control.
 *
 * Canonical implementation consumed by both `packages/web` and the hosted
 * frontend so the toggle UX stays identical across surfaces. Relies on
 * `next-themes` (a peer dependency of consumers) for state + persistence;
 * this component just calls `setTheme()`.
 *
 * Renders nothing theme-dependent until mounted so the control doesn't flash
 * the wrong selection during hydration (next-themes returns `undefined` on
 * the server).
 */

import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { Monitor, Sun, Moon } from "lucide-react";
import { cn } from "../lib/cn";

const OPTIONS = [
  { value: "system" as const, label: "System", icon: Monitor },
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
