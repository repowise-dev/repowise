import { cn } from "../lib/cn";

export interface BrandMarkProps {
  /** Canonical owl — cream strokes, drawn for dark surfaces. */
  darkSrc?: string;
  /** Plum-stroke variant for light surfaces. */
  lightSrc?: string;
  size?: number;
  alt?: string;
  className?: string;
}

/**
 * Theme-aware brand mark for packages/ui surfaces. Mirrors the web app's
 * BrandLogo: the canonical owl asset has cream strokes (drawn for dark
 * surfaces), so light mode swaps in the plum-stroke variant. Both render so
 * the theme flip is instant — visibility is CSS-only via the `.dark` class.
 */
export function BrandMark({
  darkSrc = "/repowise-logo.png",
  lightSrc = "/repowise-logo-light.png",
  size = 28,
  alt = "repowise",
  className,
}: BrandMarkProps) {
  return (
    <>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={lightSrc}
        alt={alt}
        width={size}
        height={size}
        className={cn("shrink-0 dark:hidden", className)}
      />
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={darkSrc}
        alt=""
        aria-hidden
        width={size}
        height={size}
        className={cn("shrink-0 hidden dark:block", className)}
      />
    </>
  );
}
