"use client";

/**
 * ThemedToaster — Sonner toaster that follows the active next-themes theme.
 *
 * The root layout is a server component, so the Toaster's `theme` prop can't
 * read `useTheme` there. This thin client wrapper bridges them: it passes the
 * resolved theme (light/dark, falling back to system) so toasts match the
 * themed surfaces in either mode.
 */

import { useTheme } from "next-themes";
import { Toaster } from "sonner";

export function ThemedToaster() {
  const { resolvedTheme } = useTheme();
  return (
    <Toaster
      theme={resolvedTheme === "light" ? "light" : "dark"}
      position="bottom-right"
      toastOptions={{
        style: {
          background: "var(--color-bg-elevated)",
          border: "1px solid var(--color-border-default)",
          color: "var(--color-text-primary)",
        },
      }}
    />
  );
}
