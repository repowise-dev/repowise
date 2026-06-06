"use client";

import { useState } from "react";
import Image from "next/image";
import { DotLottieReact } from "@lottiefiles/dotlottie-react";
import { cn } from "@repowise-dev/ui/lib/cn";

/**
 * Brand loading animation — the owl Lottie, centered. Falls back to a
 * pulsing static brand mark if the animation asset fails to load, so a
 * missing /owl-loading.lottie never breaks a loading state.
 */
export function OwlLoader({
  size = 160,
  label = "Loading…",
  className,
}: {
  size?: number;
  label?: string;
  className?: string;
}) {
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
          <Image
            src="/repowise-logo-light.png"
            alt=""
            aria-hidden
            width={size * 0.6}
            height={size * 0.6}
            className="dark:hidden"
          />
          <Image
            src="/repowise-logo.png"
            alt=""
            aria-hidden
            width={size * 0.6}
            height={size * 0.6}
            className="hidden dark:block"
          />
        </span>
      ) : (
        <DotLottieReact
          src="/owl-loading.json"
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
