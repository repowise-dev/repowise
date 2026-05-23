"use client";

import { useEffect, useState } from "react";
import { Info, X } from "lucide-react";

const STORAGE_PREFIX = "repowise:health:recalibration-banner-dismissed:";

export interface RecalibrationBannerProps {
  repoId: string;
}

export function RecalibrationBanner({ repoId }: RecalibrationBannerProps) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const dismissed = window.localStorage.getItem(STORAGE_PREFIX + repoId);
      if (!dismissed) setVisible(true);
    } catch {
      setVisible(true);
    }
  }, [repoId]);

  if (!visible) return null;

  const dismiss = () => {
    try {
      window.localStorage.setItem(STORAGE_PREFIX + repoId, "1");
    } catch {
      /* localStorage unavailable — just hide in this session. */
    }
    setVisible(false);
  };

  return (
    <div
      role="status"
      className="flex items-start gap-3 rounded-lg border border-[var(--color-accent-primary)]/40 bg-[var(--color-accent-muted)] px-4 py-3 text-sm"
    >
      <Info
        className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-accent-primary)]"
        aria-hidden="true"
      />
      <div className="flex-1 space-y-1">
        <p className="font-medium text-[var(--color-text-primary)]">
          Scores were recalibrated.
        </p>
        <p className="text-[var(--color-text-secondary)]">
          Organizational signals — developer congestion, knowledge
          concentration, hidden coupling — now weigh more heavily because
          they&apos;re the strongest empirical predictors of defects. Some
          files moved.
        </p>
      </div>
      <button
        type="button"
        onClick={dismiss}
        aria-label="Dismiss"
        className="rounded p-1 text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}
