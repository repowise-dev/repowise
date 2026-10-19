import * as React from "react";
import type { OwnerProfile, OwnerCoAuthor } from "@repowise-dev/types/owners";
import { OverviewSection, SectionLink } from "../overview/section";
import { OwnerCommitMix } from "./owner-commit-mix";
import { OwnerFileTable, soleCarriedCount } from "./owner-file-table";
import { OwnerFootprint } from "./owner-footprint";
import { OwnerIdentity } from "./owner-identity";
import { OwnerRelationships } from "./owner-relationships";
import { ownershipLabels, type OwnershipLabels } from "./labels";

/**
 * A person page about knowledge and handover, never a scorecard: who they
 * are, what depends on them and who could take it over, where they work, who
 * they work with, then the full file table and commit mix behind disclosure.
 * No rankings and no per-person negatives.
 *
 * Server-renderable. The off-boarding section is interactive (prompts, file
 * lists), so the host renders it as a client component and passes it in.
 */

/**
 * Positive credit for one person, from `/wins?author=` (lane L-history). The
 * endpoint is not on this branch's base yet, so this is a typed slot the host
 * fills once it lands; nothing renders without it.
 */
export interface OwnerCredit {
  filesImproved: number;
  findingsResolved: number;
  /** The window the counts cover, in days. */
  windowDays: number;
  href?: string;
}

export interface OwnerProfileViewProps {
  owner: OwnerProfile;
  /** `/repos/{id}` — the prefix for every drill-in on this page. */
  base: string;
  /** "What depends on them": the host's single-person off-boarding view. */
  dependsOn: React.ReactNode;
  credit?: OwnerCredit | undefined;
  hrefForFile?: ((filePath: string) => string) | undefined;
  hrefForModule?: ((modulePath: string) => string) | undefined;
  hrefForCoAuthor?: ((coAuthor: OwnerCoAuthor) => string) | undefined;
  LinkComponent?: React.ElementType | undefined;
  labels?: Partial<OwnershipLabels> | undefined;
}

export function OwnerProfileView({
  owner,
  base,
  dependsOn,
  credit,
  hrefForFile,
  hrefForModule,
  hrefForCoAuthor,
  LinkComponent,
  labels: overrides,
}: OwnerProfileViewProps) {
  const labels = ownershipLabels(overrides);
  const filesShown = owner.top_files.slice(0, 20);
  const soleCarried = soleCarriedCount(filesShown);
  const touched = owner.files_touched_total ?? owner.top_files.length;

  return (
    <div className="flex flex-col gap-6 sm:gap-8">
      <OwnerIdentity owner={owner} labels={labels} />

      <OverviewSection
        flush
        title={labels.dependsTitle}
        description={labels.dependsDescription}
        action={
          <SectionLink href={`${base}/owners`} LinkComponent={LinkComponent}>
            {labels.ownershipLink}
          </SectionLink>
        }
      >
        {dependsOn}
      </OverviewSection>

      <OwnerFootprint
        modules={owner.modules}
        hrefForModule={hrefForModule}
        LinkComponent={LinkComponent}
        labels={labels}
      />

      {credit && <OwnerCreditSection credit={credit} LinkComponent={LinkComponent} labels={labels} />}

      <OwnerRelationships
        coAuthors={owner.co_authors}
        total={owner.co_authors_total ?? owner.co_authors.length}
        hrefForCoAuthor={hrefForCoAuthor}
        LinkComponent={LinkComponent}
        labels={labels}
      />

      <OverviewSection
        title={labels.filesCarryTitle}
        collapsible
        defaultOpen={false}
        hint={labels.filesCarryHint(filesShown.length, touched)}
        description={labels.filesCarryDescription(soleCarried, filesShown.length)}
      >
        <OwnerFileTable files={filesShown} hrefForFile={hrefForFile} LinkComponent={LinkComponent} labels={labels} />
      </OverviewSection>

      <OverviewSection title={labels.commitMixTitle} collapsible defaultOpen={false}>
        <OwnerCommitMix categories={owner.commit_categories} labels={labels} />
      </OverviewSection>
    </div>
  );
}

function OwnerCreditSection({
  credit,
  LinkComponent,
  labels,
}: {
  credit: OwnerCredit;
  LinkComponent?: React.ElementType | undefined;
  labels: OwnershipLabels;
}) {
  if (credit.filesImproved === 0 && credit.findingsResolved === 0) return null;
  return (
    <OverviewSection
      title={labels.creditTitle}
      {...(credit.href
        ? {
            action: (
              <SectionLink href={credit.href} LinkComponent={LinkComponent}>
                {labels.creditLink}
              </SectionLink>
            ),
          }
        : {})}
    >
      <p className="text-[15px] leading-relaxed text-[var(--color-text-secondary)]">
        {labels.creditSentence(credit.windowDays, credit.filesImproved, credit.findingsResolved)}
      </p>
    </OverviewSection>
  );
}
