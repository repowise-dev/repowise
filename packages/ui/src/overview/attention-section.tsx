import * as React from "react";
import type { OverviewSummaryResponse } from "@repowise-dev/types/overview";
import { getDefaultHref } from "../dashboard/attention-href";
import { formatNumber } from "../lib/format";
import { AttentionAreas } from "./attention-areas";
import { AttentionRows } from "./attention-rows";
import { OverviewSection } from "./section";

/** Ranked rows shown when the server predates the area rollup. */
const ATTENTION_ROWS = 6;

export interface AttentionSectionProps {
  summary: OverviewSummaryResponse;
  /** Link prefix for this host. */
  base: string;
  /** Fold it as the inventory behind "Do next" instead of leading with it. */
  inventory?: boolean;
  LinkComponent?: React.ElementType | undefined;
}

/**
 * Every detector's open findings by area of work. Leads the page on a host
 * without "Do next"; folds into an inventory below it otherwise. Renders
 * nothing when nothing is open.
 */
export function AttentionSection({
  summary,
  base,
  inventory = false,
  LinkComponent,
}: AttentionSectionProps) {
  // The list is capped per source server-side, so its length is not a total.
  // Falling back to the row count on a server that predates the summary keeps
  // the old (correct-for-that-payload) behaviour rather than rendering
  // nothing.
  const attentionTotal = summary.attention_summary?.total ?? summary.attention.length;
  const areas = summary.attention_summary?.areas ?? [];
  const hasAttention = areas.length > 0 || summary.attention.length > 0;

  if (!hasAttention) return null;
  return (
    <OverviewSection
      title={inventory ? "Everything we found" : "Needs attention"}
      description={
        inventory
          ? "Every detector's open count by area. The actions above are the part worth doing."
          : "Each area of work, led by the worst thing in it."
      }
      collapsible={inventory}
      defaultOpen={!inventory}
      hint={inventory ? `${formatNumber(attentionTotal)} open` : undefined}
      // No section link: these span seven pages, so there is no single
      // destination that holds "all of them", and a count linking to one
      // of them would be a lie about where the rest live.
      action={
        <span className="font-mono text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
          {formatNumber(attentionTotal)} open
        </span>
      }
    >
      {areas.length > 0 ? (
        <AttentionAreas areas={areas} prefix={base} LinkComponent={LinkComponent} />
      ) : (
        // A server predating the area rollup still sends ranked items, and
        // rendering those is strictly better than rendering nothing.
        <AttentionRows
          items={summary.attention.slice(0, ATTENTION_ROWS)}
          hrefFor={(item) => getDefaultHref(item, base)}
          LinkComponent={LinkComponent}
        />
      )}
    </OverviewSection>
  );
}
