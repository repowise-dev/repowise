"use client";

import * as React from "react";
import type { OwnersSummary } from "@repowise-dev/types/owners";
import { AiPromptButton } from "../health/ai-prompt-button";
import { useAiPromptAction } from "../health/ai-prompt-action";
import { ReadsColumn, type ReadItem } from "../overview/reads-column";
import { PageLede } from "../shared/page-lede";
import { WELL_SHARED_MIN, type KnowledgeCoverage } from "./knowledge-coverage";
import { ownershipLabels, type OwnershipLabels } from "./labels";
import { spreadPromptRequest } from "./prompt-requests";

export type OwnershipTab = "risks" | "loss" | "offboarding";

export interface OwnershipLedeProps {
  coverage: KnowledgeCoverage;
  summary: OwnersSummary;
  /** Link to an Ownership tab, e.g. `?tab=risks`, with an off-boarding scenario. */
  hrefForTab: (tab: OwnershipTab, leave?: string[]) => string;
  repoName?: string | undefined;
  LinkComponent?: React.ElementType | undefined;
  labels?: Partial<OwnershipLabels> | undefined;
}

/** Whole percent, capped below 100 so a share short of everything never reads "100%". */
const pct = (share: number) => `${share >= 1 ? 100 : Math.min(99, Math.round(share * 100))}%`;

/** The reads beside the lede, each present only when it has something to point at. */
function ownershipReads(
  summary: OwnersSummary,
  hrefForTab: OwnershipLedeProps["hrefForTab"],
  labels: OwnershipLabels,
): ReadItem[] {
  const reads: ReadItem[] = [];
  if (summary.single_owner_hotspots > 0) {
    reads.push({
      key: "hotspots",
      label: labels.readHotspots,
      value: summary.single_owner_hotspots.toLocaleString(),
      unit: labels.readHotspotsUnit(summary.single_owner_files),
      why: labels.readHotspotsWhy,
      href: hrefForTab("risks"),
    });
  }
  if (summary.quiet_owners_total > 0) {
    reads.push({
      key: "quiet",
      label: labels.readQuiet,
      value: summary.quiet_owners_total.toLocaleString(),
      unit: labels.readQuietUnit(summary.quiet_owners_total),
      why: labels.readQuietWhy(summary.activity_window_days),
      href: hrefForTab("loss"),
    });
  }
  const tf = summary.truck_factor;
  if (tf != null) {
    // The truck factor is the fewest top owners covering half the owned files,
    // so within the concentration list those are the people; the read links to
    // their scenario without naming anyone.
    const owners = summary.concentration.owners;
    const keys = tf <= owners.length ? owners.slice(0, tf).map((o) => o.key) : [];
    reads.push({
      key: "truck",
      label: labels.readTruck,
      value: tf.toLocaleString(),
      why: labels.readTruckWhy,
      href: hrefForTab("offboarding", keys),
    });
  }
  return reads;
}

/** The coverage figure and its sentence; `share` is known here. */
function CoverageFigure({
  coverage,
  share,
  days,
  labels,
  onPrompt,
}: {
  coverage: KnowledgeCoverage;
  share: number;
  days: number;
  labels: OwnershipLabels;
  onPrompt: () => void;
}) {
  const byLines = coverage.basis === "nloc";
  const good = share >= WELL_SHARED_MIN;
  const single = coverage.singleHolderFiles;
  return (
    <PageLede
      label={labels.coverageLabel}
      labelHint={labels.coverageHint(byLines, days)}
      value={pct(share)}
      unit={labels.coverageUnit(byLines)}
      {...(good ? { valueColor: "var(--color-success)", band: { label: labels.wellShared, color: "var(--color-success)" } } : {})}
    >
      <p>
        {labels.coverageSentence(pct(share), byLines, days)}
        {single > 0 && ` ${labels.singleHolderSentence(single, coverage.files)}`}
      </p>
      {single > 0 && <AiPromptButton className="mt-3" onClick={onPrompt} label={labels.agentPrompt} />}
    </PageLede>
  );
}

/**
 * The Ownership page's one figure: how much of the code that is changing has
 * a second person who knows it. Good news reads calm green; a low share reads
 * plain, never as an alarm. The primary action hands the single-person
 * changing files to the reader's agent for a plan to spread what they know.
 */
export function OwnershipLede({
  coverage,
  summary,
  hrefForTab,
  repoName,
  LinkComponent,
  labels: overrides,
}: OwnershipLedeProps) {
  const labels = React.useMemo(() => ownershipLabels(overrides), [overrides]);
  const { openPrompt, promptModal } = useAiPromptAction();
  const reads = ownershipReads(summary, hrefForTab, labels);
  const days = summary.activity_window_days;

  return (
    <section className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_300px] lg:gap-10">
      {coverage.share == null ? (
        <p className="max-w-[62ch] text-[15px] leading-relaxed text-[var(--color-text-secondary)]">
          {labels.noChanges(days)}
        </p>
      ) : (
        <CoverageFigure
          coverage={coverage}
          share={coverage.share}
          days={days}
          labels={labels}
          onPrompt={() =>
            openPrompt(
              spreadPromptRequest(coverage.singleHolderAreas, labels, {
                // The prompt itself stays English: it is written for the agent.
                spreadReason: `each one changed in the last ${days} days`,
                repoName,
              }),
            )
          }
        />
      )}
      {reads.length > 0 && <ReadsColumn items={reads} LinkComponent={LinkComponent} />}
      {promptModal}
    </section>
  );
}
