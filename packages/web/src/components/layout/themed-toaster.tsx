"use client";

/**
 * ThemedToaster — shared token-themed toaster that follows the active
 * next-themes theme. The root layout is a server component, so this thin
 * client wrapper bridges `useTheme` to the presentational Toaster in
 * packages/ui.
 */

import { useTheme } from "next-themes";
import { Toaster } from "@repowise-dev/ui/shared";

export function ThemedToaster() {
  const { resolvedTheme } = useTheme();
  return <Toaster theme={resolvedTheme === "light" ? "light" : "dark"} />;
}
