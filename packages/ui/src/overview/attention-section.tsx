import * as React from "react";
import type { OverviewSummaryResponse } from "@repowise-dev/types/overview";
import { getDefaultHref } from "../dashboard/attention-href";
import { formatNumber } from "../lib/format";
import { AttentionAreas, type AttentionAreasCopy } from "./attention-areas";
import { AttentionRows } from "./attention-rows";
import { inventoryRows, type InventoryCopy } from "./overview-model";
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
  copy?: Partial<AttentionSectionCopy> | undefined;
}

/** Headings, counts and row words. English by default. */
export interface AttentionSectionCopy {
  title: string;
  description: string;
  inventoryTitle: string;
  inventoryDescription: string;
  scopeAreas: (areas: number) => string;
  scopeOpen: (open: number) => string;
  areas?: Partial<AttentionAreasCopy> | undefined;
  inventory?: Partial<InventoryCopy> | undefined;
}

export const ATTENTION_SECTION_COPY: AttentionSectionCopy = {
  title: "Needs attention",
  description: "Each area of work, led by the worst thing in it.",
  inventoryTitle: "Everything we found",
  inventoryDescription:
    "Every detector's open count by area. The actions above are the part worth doing.",
  scopeAreas: (areas) => `${formatNumber(areas)} ${areas === 1 ? "area" : "areas"}`,
  scopeOpen: (open) => `${formatNumber(open)} open`,
};

/** Areas count different things, so the header counts areas, not their sum. */
function scopeLabel(
  summary: OverviewSummaryResponse,
  areaCount: number,
  c: AttentionSectionCopy,
): string {
  if (areaCount > 0) return c.scopeAreas(areaCount);
  return c.scopeOpen(summary.attention_summary?.total ?? summary.attention.length);
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
  copy,
}: AttentionSectionProps) {
  const c = { ...ATTENTION_SECTION_COPY, ...copy };
  // Counts read from the same basis as the explore cards and the pages they
  // link to. The areas count different things (findings, files,
  // opportunities), so the header counts areas rather than summing them.
  const areas = inventoryRows(summary, c.inventory);
  const hasAttention = areas.length > 0 || summary.attention.length > 0;
  const scope = scopeLabel(summary, areas.length, c);

  if (!hasAttention) return null;
  return (
    <OverviewSection
      title={inventory ? c.inventoryTitle : c.title}
      description={inventory ? c.inventoryDescription : c.description}
      collapsible={inventory}
      defaultOpen={!inventory}
      hint={inventory ? scope : undefined}
      // No section link: these span seven pages, so there is no single
      // destination that holds "all of them", and a count linking to one
      // of them would be a lie about where the rest live.
      action={
        <span className="font-mono text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
          {scope}
        </span>
      }
    >
      {areas.length > 0 ? (
        <AttentionAreas areas={areas} prefix={base} LinkComponent={LinkComponent} copy={c.areas} />
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
