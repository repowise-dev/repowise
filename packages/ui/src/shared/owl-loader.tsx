"use client";

import { useState } from "react";
import { DotLottieReact } from "@lottiefiles/dotlottie-react";
import { cn } from "../lib/cn";
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
 * Brand loading animation — the owl Lottie, centered. Falls back to a
 * pulsing static brand mark if the animation asset fails to load, so a
 * missing lottie asset never breaks a loading state. The asset itself stays
 * per-app (lazy-fetched, CDN-cached) — only the component is shared.
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

  return (
    <div
      role="status"
      aria-label={label}
      className={cn(
        "flex min-h-[50vh] flex-col items-center justify-center gap-3",
        className,
      )}
    >
      {failed ? (
        <span className="motion-safe:animate-pulse">
          <BrandMark
            darkSrc={logoDarkSrc}
            lightSrc={logoLightSrc}
            size={size * 0.6}
            alt=""
          />
        </span>
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
