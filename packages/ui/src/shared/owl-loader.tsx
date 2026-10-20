"use client";

import { cn } from "../lib/cn";
import { OrbLoader } from "./orb-loader";

/** @deprecated Use `OrbLoaderProps`. */
export interface OwlLoaderProps {
  /** @deprecated Ignored; the owl animation is gone. */
  src?: string;
  /** @deprecated Ignored. */
  logoDarkSrc?: string;
  /** @deprecated Ignored. */
  logoLightSrc?: string;
  /** @deprecated Ignored; the orb renders at its 64px region size. */
  size?: number;
  label?: string;
  className?: string;
}

/**
 * @deprecated Use `OrbLoader` for unknown-shape waits, or a `PageSkeleton`
 * for a page. Kept only so consumers pinned to the old export keep
 * compiling; it renders the region orb and keeps the old `min-h-[50vh]`
 * frame so their layouts do not jump.
 */
export function OwlLoader({ label = "Loading…", className }: OwlLoaderProps) {
  return <OrbLoader fill label={label} className={cn("min-h-[50vh]", className)} />;
}
