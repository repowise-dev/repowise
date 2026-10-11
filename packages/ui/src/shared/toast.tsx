"use client";

import { Toaster as SonnerToaster, toast } from "sonner";

/**
 * Token-themed sonner toaster. Framework-neutral: the host app passes the
 * resolved theme (e.g. from next-themes) instead of this component reading it.
 * Mount once at the app shell level; fire toasts via the re-exported `toast`.
 */
export interface ToasterProps {
  theme?: "light" | "dark" | undefined;
  position?: React.ComponentProps<typeof SonnerToaster>["position"] | undefined;
}

export function Toaster({ theme = "dark", position = "bottom-right" }: ToasterProps) {
  return (
    <SonnerToaster
      theme={theme}
      position={position}
      style={{ zIndex: "var(--z-toast)" } as React.CSSProperties}
      toastOptions={{
        style: {
          background: "var(--color-bg-elevated)",
          border: "1px solid var(--color-border-default)",
          color: "var(--color-text-primary)",
        },
      }}
    />
  );
}

export { toast };
