/**
 * Build-time replacement for the `next-themes` peer the shared UI package
 * expects (aliased in vite.config). Inside a webview the theme is dictated by
 * the editor, so `setTheme` is a no-op and the resolved theme mirrors the
 * body classes VS Code maintains.
 */

import { useSyncExternalStore, type ReactNode } from "react";
import { getThemeKind, setThemeOverride, subscribeTheme } from "../runtime/theme";

export interface UseThemeProps {
  theme: string | undefined;
  resolvedTheme: string | undefined;
  setTheme: (theme: string) => void;
  themes: string[];
  systemTheme: string | undefined;
}

export function useTheme(): UseThemeProps {
  const kind = useSyncExternalStore(subscribeTheme, getThemeKind, () => "light" as const);
  return {
    theme: kind,
    resolvedTheme: kind,
    setTheme: (theme: string) => {
      // In-view toggles override until the next editor theme change, which
      // the body-class observer applies over this.
      if (theme === "dark" || theme === "light") setThemeOverride(theme);
    },
    themes: ["light", "dark"],
    systemTheme: kind,
  };
}

export function ThemeProvider(props: { children: ReactNode }): ReactNode {
  return props.children;
}
