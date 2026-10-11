"use client";

/**
 * ThemeProvider — wraps next-themes for the product dashboard.
 *
 * `attribute="class"` writes the resolved theme onto <html> as `class="dark"`
 * / `class="light"`, which the theme token contract keys off
 * (`:root` = light, `.dark` = dark overrides in @repowise-dev/ui globals).
 *
 * `defaultTheme="light"` — fresh visitors with no stored preference land in
 * Light; Dark is opt-in via the shared ThemeToggle and persists across reloads
 * (next-themes writes the choice to the `theme` localStorage key). Explicit
 * two-state — no "System" option (product decision; the toggle migrates stale
 * persisted "system" values). `disableTransitionOnChange` prevents a
 * color-transition smear when the user flips themes.
 */

import { ThemeProvider as NextThemesProvider } from "next-themes";
import type { ReactNode } from "react";

export function ThemeProvider({ children }: { children: ReactNode }) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="light"
      enableSystem={false}
      themes={["light", "dark"]}
      disableTransitionOnChange
    >
      {children}
    </NextThemesProvider>
  );
}
