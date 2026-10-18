"use client";

import * as React from "react";
import type { OwnersSummary } from "@repowise-dev/types/owners";
import { AiPromptButton } from "../health/ai-prompt-button";
import { AGENT_PROMPT_LABEL, useAiPromptAction } from "../health/ai-prompt-action";
import { ReadsColumn, type ReadItem } from "../overview/reads-column";
import { PageLede } from "../shared/page-lede";
import { buildHandoverPrompt, riskPromptAreas } from "./handover-prompt";
import { WELL_SHARED_MIN, type KnowledgeCoverage } from "./knowledge-coverage";

export type OwnershipTab = "risks" | "loss" | "offboarding";

export interface OwnershipLedeProps {
  coverage: KnowledgeCoverage;
  summary: OwnersSummary;
  /** Link to an Ownership tab, e.g. `?tab=risks`, with an off-boarding scenario. */
  hrefForTab: (tab: OwnershipTab, leave?: string[]) => string;
  repoName?: string | undefined;
  LinkComponent?: React.ElementType | undefined;
}

const pct = (share: number) => `${Math.round(share * 100)}%`;

/** The reads beside the lede, each present only when it has something to point at. */
function ownershipReads(summary: OwnersSummary, hrefForTab: OwnershipLedeProps["hrefForTab"]): ReadItem[] {
  const reads: ReadItem[] = [];
  if (summary.single_owner_hotspots > 0) {
    reads.push({
      key: "hotspots",
      label: "Single-author hotspots",
      value: summary.single_owner_hotspots.toLocaleString(),
      unit: `of ${summary.single_owner_files.toLocaleString()} single-author files`,
      why: "Busy files only one person has written. The cheapest place to get a second reader.",
      href: hrefForTab("risks"),
    });
  }
  if (summary.quiet_owners_total > 0) {
    reads.push({
      key: "quiet",
      label: "Quiet owners of changing code",
      value: summary.quiet_owners_total.toLocaleString(),
      unit: summary.quiet_owners_total === 1 ? "person" : "people",
      why: `No commit in ${summary.activity_window_days} days, yet files they own changed in that time.`,
      href: hrefForTab("loss"),
    });
  }
  const tf = summary.truck_factor;
  if (tf != null) {
    // The truck factor is the fewest top owners covering half the owned files,
    // so within the concentration list those are the people; the read links to
    // their scenario without naming anyone.
    const keys = tf <= summary.concentration.owners.length ? summary.concentration.owners.slice(0, tf).map((o) => o.key) : [];
    reads.push({
      key: "truck",
      label: "Fewest people holding half the owned files",
      value: tf.toLocaleString(),
      why: "The truck factor. Open the off-boarding view to see what depends on them.",
      href: hrefForTab("offboarding", keys),
    });
  }
  return reads;
}

/**
 * The Ownership page's one figure: how much of the code that is changing has
 * a second person who knows it. Good news reads calm green; a low share reads
 * plain, never as an alarm. The primary action hands the single-person
 * changing files to the reader's agent for a plan to spread what they know.
 */
export function OwnershipLede({ coverage, summary, hrefForTab, repoName, LinkComponent }: OwnershipLedeProps) {
  const { openPrompt, promptModal } = useAiPromptAction();
  const reads = ownershipReads(summary, hrefForTab);
  const { share } = coverage;
  const good = share != null && share >= WELL_SHARED_MIN;
  const scope = coverage.basis === "nloc" ? "of the code" : "of the files";

  const spreadPrompt = () => {
    const areas = riskPromptAreas(coverage.singleHolderAreas);
    const people = Array.from(new Set(coverage.singleHolderAreas.flatMap((a) => a.files.map((f) => f.holder.name))));
    openPrompt({
      title: "Spread the knowledge",
      description: `A plan for a second person to learn ${coverage.singleHolderFiles.toLocaleString()} changing ${coverage.singleHolderFiles === 1 ? "file" : "files"} one person holds.`,
      getPrompt: (flavor) =>
        buildHandoverPrompt({
          purpose: "spread",
          people,
          areas,
          flavor,
          spreadReason: `each one changed in the last ${summary.activity_window_days} days`,
          ...(repoName ? { repoName } : {}),
        }),
    });
  };

  return (
    <section className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_300px] lg:gap-10">
      {share == null ? (
        <p className="max-w-[62ch] text-[15px] leading-relaxed text-[var(--color-text-secondary)]">
          No code with history changed in the last {summary.activity_window_days} days, so there is no moving
          code to measure knowledge coverage on.
        </p>
      ) : (
        <PageLede
          label="Knowledge coverage"
          labelHint={`Share ${scope} changed in the last ${summary.activity_window_days} days in files two or more people hold (bus factor 2 or more), ${coverage.basis === "nloc" ? "weighted by lines of code" : "by file count"}. Files with no indexed history are left out.`}
          value={pct(share)}
          unit={`${scope} changed lately`}
          {...(good ? { valueColor: "var(--color-success)", band: { label: "Well shared", color: "var(--color-success)" } } : {})}
        >
          <p>
            {pct(share)} {scope} changed in the last {summary.activity_window_days} days has two or more people
            who know it: {coverage.sharedFiles.toLocaleString()} of {coverage.files.toLocaleString()} changing
            files.
            {coverage.singleHolderFiles > 0 &&
              ` The other ${coverage.singleHolderFiles.toLocaleString()} have one main author; your agent can plan how a second person learns them.`}
          </p>
          {coverage.singleHolderFiles > 0 && (
            <AiPromptButton className="mt-3" onClick={spreadPrompt} label={AGENT_PROMPT_LABEL} />
          )}
        </PageLede>
      )}
      {reads.length > 0 && <ReadsColumn items={reads} LinkComponent={LinkComponent} />}
      {promptModal}
    </section>
  );
}
