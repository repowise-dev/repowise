"use client";

/**
 * ThemeProvider — wraps next-themes for the product dashboard.
 *
 * `attribute="class"` writes the resolved theme onto <html> as `class="dark"`
 * / `class="light"`, which the October Sunset token contract keys off
 * (`:root` = light, `.dark` = dark overrides in @repowise-dev/ui globals).
 *
 * `defaultTheme="dark"` preserves the product's historical default; Light is
 * opt-in via the shared ThemeToggle. Explicit two-state — no "System" option
 * (product decision; the toggle migrates stale persisted "system" values).
 * `disableTransitionOnChange` prevents a color-transition smear when the
 * user flips themes.
 */

import { ThemeProvider as NextThemesProvider } from "next-themes";
import type { ReactNode } from "react";

export function ThemeProvider({ children }: { children: ReactNode }) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="dark"
      enableSystem={false}
      themes={["light", "dark"]}
      disableTransitionOnChange
    >
      {children}
    </NextThemesProvider>
  );
}
