"use client";

import { useState } from "react";
import { DotLottieReact } from "@lottiefiles/dotlottie-react";
import { cn } from "../lib/cn";
import { usePrefersReducedMotion } from "../hooks/use-prefers-reduced-motion";
import { BrandMark } from "./brand-mark";

export interface OwlLoaderProps {
  /** Path to the owl Lottie asset, served from the consuming app's public/. */
  src?: string;
  /** Fallback brand-mark assets, shown if the animation fails to load. */
  logoDarkSrc?: string;
  logoLightSrc?: string;
  size?: number;
  label?: string;
  className?: string;
}

/**
 * Brand loading animation — the owl Lottie, centered. Falls back to the
 * static brand mark if the animation asset fails to load, so a missing
 * lottie asset never breaks a loading state. The asset itself stays per-app
 * (lazy-fetched, CDN-cached) — only the component is shared.
 *
 * Under reduced motion the same still mark is the equivalent still, and the
 * Lottie is never mounted — a paused animation would still pay for the WASM
 * runtime and the JSON fetch.
 */
export function OwlLoader({
  src = "/owl-loading.json",
  logoDarkSrc = "/repowise-logo.png",
  logoLightSrc = "/repowise-logo-light.png",
  size = 160,
  label = "Loading…",
  className,
}: OwlLoaderProps) {
  const [failed, setFailed] = useState(false);
  const reducedMotion = usePrefersReducedMotion();
  const still = failed || reducedMotion;

  return (
    <div
      role="status"
      aria-label={label}
      className={cn(
        "flex min-h-[50vh] flex-col items-center justify-center gap-3",
        className,
      )}
    >
      {still ? (
        <BrandMark
          darkSrc={logoDarkSrc}
          lightSrc={logoLightSrc}
          size={size * 0.6}
          alt=""
        />
      ) : (
        <DotLottieReact
          src={src}
          loop
          autoplay
          style={{ width: size, height: size }}
          dotLottieRefCallback={(dotLottie) => {
            dotLottie?.addEventListener("loadError", () => setFailed(true));
          }}
        />
      )}
      <span className="text-sm text-[var(--color-text-tertiary)]">{label}</span>
    </div>
  );
}
