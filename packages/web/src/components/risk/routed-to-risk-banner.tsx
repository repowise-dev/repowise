"use client";

import Link from "next/link";
import { ShieldAlert, ArrowRight } from "lucide-react";

interface RoutedToRiskBannerProps {
  repoId: string;
  /** Which Risk tab this old route now lives under. */
  tab: "heatmap" | "hotspots" | "dead-code" | "impact";
  /** Optional override for the human label (defaults to the tab id). */
  tabLabel?: string;
}

const TAB_TITLE: Record<RoutedToRiskBannerProps["tab"], string> = {
  heatmap: "Heatmap",
  hotspots: "Hotspots",
  "dead-code": "Dead code",
  impact: "Impact analyzer",
};

/**
 * Soft-consolidation banner: legacy /hotspots, /ownership, /dead-code,
 * /blast-radius routes still resolve, but the banner nudges users toward
 * the unified /repos/[id]/risk page where these now live as tabs. This
 * keeps deep links + bookmarks working while the IA migration is rolled out.
 */
export function RoutedToRiskBanner({ repoId, tab, tabLabel }: RoutedToRiskBannerProps) {
  const target = `/repos/${repoId}/risk?tab=${tab}`;
  const label = tabLabel ?? TAB_TITLE[tab];
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-[var(--color-accent-primary)]/30 bg-[var(--color-accent-primary)]/8 px-3 py-2 text-xs text-[var(--color-text-secondary)]">
      <ShieldAlert className="h-3.5 w-3.5 shrink-0 text-[var(--color-accent-primary)]" />
      <span className="flex-1 min-w-0">
        This view now lives under{" "}
        <Link
          href={target}
          className="font-medium text-[var(--color-accent-primary)] hover:underline"
        >
          Risk → {label}
        </Link>
        . Existing bookmarks keep working.
      </span>
      <Link
        href={target}
        className="inline-flex shrink-0 items-center gap-1 rounded-md border border-[var(--color-accent-primary)]/40 bg-[var(--color-bg-elevated)] px-2 py-1 font-medium text-[var(--color-accent-primary)] transition hover:bg-[var(--color-accent-primary)]/10"
      >
        Open Risk <ArrowRight className="h-3 w-3" />
      </Link>
    </div>
  );
}
