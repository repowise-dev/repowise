"use client";

/**
 * Runtime design-token resolution for canvas / library renderers.
 *
 * Most components consume `var(--color-*)` directly and inherit light/dark for
 * free. But renderers that paint to a `<canvas>` or hand colors to a library
 * (Mermaid, Sigma, exported SVG strings) can't use `var()` — they need the
 * *computed* color, read from the live `:root` style, and must re-read +
 * re-render when the user flips the theme.
 *
 * `useThemeVersion()` returns a counter that bumps whenever the `.dark` class
 * (or inline style) on <html> changes — put it in a dependency array to force a
 * re-render on theme switch. `resolveToken()` reads one computed custom
 * property; `resolveTokens()` reads a map of them. Read inside an effect/memo
 * keyed on the theme version so the values track the active theme.
 */

import { useEffect, useMemo, useState } from "react";

/** Read a single CSS custom property off <html>, resolved to its computed value. */
export function resolveToken(name: string, fallback = ""): string {
  if (typeof window === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

/** Resolve a `{ key: "--color-var" }` spec to `{ key: "#computed" }`. */
export function resolveTokens<K extends string>(spec: Record<K, string>): Record<K, string> {
  const out = {} as Record<K, string>;
  for (const key in spec) out[key] = resolveToken(spec[key]);
  return out;
}

/**
 * A counter that increments whenever the document theme changes (the `.dark`
 * class or inline `style` on <html> mutates). Use as a dependency so canvas
 * renderers re-resolve tokens and repaint on light/dark switch.
 */
export function useThemeVersion(): number {
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const root = document.documentElement;
    const observer = new MutationObserver(() => setVersion((v) => v + 1));
    observer.observe(root, { attributes: true, attributeFilter: ["class", "style", "data-theme"] });
    return () => observer.disconnect();
  }, []);
  return version;
}

// --------------------------------------------------------------------------
// Community families (graph clustering palette)
// --------------------------------------------------------------------------

/** Number of community families defined in globals.css (`--color-community-1..N`). */
export const COMMUNITY_FAMILY_COUNT = 12;

/** A resolved community color pair: `hub` for centroids, `satellite` for leaves. */
export interface CommunityFamily {
  hub: string;
  satellite: string;
}

/**
 * Resolve the warm community family for a given community id, cycling through
 * the 12 `--color-community-*` token pairs. Reads the *computed* tokens so the
 * canvas gets theme-correct hex. Call inside an effect/memo keyed on
 * {@link useThemeVersion} so values track the active theme. On SSR (no window)
 * the token resolves to "" and the satellite falls back to the hub.
 */
export function getCommunityFamily(communityId: number): CommunityFamily {
  const n = (((communityId % COMMUNITY_FAMILY_COUNT) + COMMUNITY_FAMILY_COUNT) %
    COMMUNITY_FAMILY_COUNT) + 1;
  const resolved = resolveTokens({
    hub: `--color-community-${n}`,
    satellite: `--color-community-${n}-soft`,
  });
  return {
    hub: resolved.hub,
    satellite: resolved.satellite || resolved.hub,
  };
}

/**
 * Hook variant: returns a stable `getCommunityFamily`-style resolver that
 * re-reads the computed tokens whenever the theme flips. Use in renderers that
 * paint community colors to a canvas/library so they repaint on light/dark
 * switch (mirrors the Mermaid/C4 token-resolution pattern).
 */
export function useCommunityFamilies(): (communityId: number) => CommunityFamily {
  const version = useThemeVersion();
  return useMemo(() => {
    // Pre-resolve all 12 families once per theme version; the returned closure
    // is a cheap cyclic lookup with no per-call getComputedStyle cost.
    void version;
    const families: CommunityFamily[] = [];
    for (let i = 0; i < COMMUNITY_FAMILY_COUNT; i++) {
      families.push(getCommunityFamily(i));
    }
    return (communityId: number) => {
      const idx = (((communityId % COMMUNITY_FAMILY_COUNT) + COMMUNITY_FAMILY_COUNT) %
        COMMUNITY_FAMILY_COUNT);
      return families[idx] ?? families[0] ?? { hub: "", satellite: "" };
    };
  }, [version]);
}
