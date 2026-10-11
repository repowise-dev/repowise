"use client";

import { AlertTriangle } from "lucide-react";
import type { DocPage } from "@repowise-dev/types/docs";

export interface DriftBannerProps {
  pages: DocPage[];
  /** Inclusive — shown only when count > threshold (default 0). */
  threshold?: number;
}

export function DriftBanner({ pages, threshold = 0 }: DriftBannerProps) {
  const drift = pages.filter((p) => p.freshness_status === "outdated").length;
  const stale = pages.filter((p) => p.freshness_status === "stale").length;
  const total = drift + stale;
  if (total <= threshold) return null;

  return (
    <div
      role="status"
      className="flex items-start gap-3 rounded-lg border border-[var(--color-warning)]/40 bg-[var(--color-warning)]/10 px-4 py-3"
    >
      <AlertTriangle className="h-5 w-5 text-[var(--color-warning)] shrink-0 mt-0.5" />
      <div className="text-sm text-[var(--color-text-primary)]">
        <p className="font-medium">
          {drift} outdated · {stale} stale
        </p>
        <p className="text-xs text-[var(--color-text-secondary)] mt-0.5">
          Source code has moved past the snapshot for these pages. Re-run generation to refresh.
        </p>
      </div>
    </div>
  );
}
