/**
 * Build-time replacement for @lottiefiles/dotlottie-react (aliased in
 * vite.config). The real component fetches a WASM renderer and a Lottie JSON
 * at runtime; both are blocked by the webview CSP and only produce console
 * noise. A token-driven pulse stands in for the brand animation.
 */

import type { CSSProperties } from "react";

export function DotLottieReact(props: { style?: CSSProperties; [key: string]: unknown }) {
  return (
    <div
      style={props.style}
      className="flex items-center justify-center"
      aria-hidden
    >
      <div className="h-10 w-10 animate-pulse rounded-full border-4 border-[var(--color-accent-muted)] border-t-[var(--color-accent-primary)]" />
    </div>
  );
}
