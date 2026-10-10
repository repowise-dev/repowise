"use client";

/**
 * ThemeToggle: one icon button that shows the theme in effect and switches to
 * the other on click.
 *
 * Relies on `next-themes` (a peer dependency of consumers) for state and
 * persistence. Deliberately two-state, no "System" option. Consumers set
 * `enableSystem={false}`; the mount effect migrates any stale persisted
 * "system" value to light. An explicit light or dark choice is never rewritten.
 *
 * Until mounted the button renders a neutral placeholder of the same size, so
 * server and first client markup match.
 */

import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { Sun, Moon } from "lucide-react";
import { cn } from "../lib/cn";

export interface ThemeToggleProps {
  /** Accessible name and tooltip while light is active. */
  switchToDarkLabel?: string;
  /** Accessible name and tooltip while dark is active. */
  switchToLightLabel?: string;
  /** Accepted for backwards compatibility; the control is always icon-only. */
  compact?: boolean;
  className?: string;
}

const BUTTON =
  "inline-flex h-8 w-8 items-center justify-center rounded-md text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-wash-hover)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]";

export function ThemeToggle({
  switchToDarkLabel = "Switch to dark theme",
  switchToLightLabel = "Switch to light theme",
  className,
}: ThemeToggleProps) {
  const { theme, resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  // Migrate a persisted "system" (or any unknown) theme to the explicit light
  // default. Only fires for non light/dark values.
  useEffect(() => {
    if (mounted && theme !== "light" && theme !== "dark") setTheme("light");
  }, [mounted, theme, setTheme]);

  if (!mounted) {
    return <span aria-hidden className={cn("inline-block h-8 w-8", className)} />;
  }

  const isDark = resolvedTheme === "dark";
  const label = isDark ? switchToLightLabel : switchToDarkLabel;
  const Icon = isDark ? Moon : Sun;

  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={() => setTheme(isDark ? "light" : "dark")}
      className={cn(BUTTON, className)}
    >
      <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />
    </button>
  );
}
