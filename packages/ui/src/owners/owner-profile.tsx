import * as React from "react";
import type { OwnerProfile, OwnerCoAuthor } from "@repowise-dev/types/owners";
import { OverviewSection, SectionLink } from "../overview/section";
import { OwnerCommitMix } from "./owner-commit-mix";
import { OwnerFileTable, soleCarriedCount } from "./owner-file-table";
import { OwnerFootprint } from "./owner-footprint";
import { OwnerIdentity } from "./owner-identity";
import { OwnerRelationships } from "./owner-relationships";

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
}: OwnerProfileViewProps) {
  const filesShown = owner.top_files.slice(0, 20);
  const soleCarried = soleCarriedCount(filesShown);
  const touched = owner.files_touched_total ?? owner.top_files.length;

  return (
    <div className="flex flex-col gap-6 sm:gap-8">
      <OwnerIdentity owner={owner} />

      <OverviewSection
        flush
        title="What depends on them"
        description="Files where this person made most of the commits, so the people who stay would hold under half of the history, and who is best placed to take each directory over."
        action={
          <SectionLink href={`${base}/owners`} LinkComponent={LinkComponent}>
            Ownership
          </SectionLink>
        }
      >
        {dependsOn}
      </OverviewSection>

      <OwnerFootprint modules={owner.modules} hrefForModule={hrefForModule} LinkComponent={LinkComponent} />

      {credit && <OwnerCreditSection credit={credit} LinkComponent={LinkComponent} />}

      <OwnerRelationships
        coAuthors={owner.co_authors}
        total={owner.co_authors_total ?? owner.co_authors.length}
        hrefForCoAuthor={hrefForCoAuthor}
        LinkComponent={LinkComponent}
      />

      <OverviewSection
        title="Files they carry"
        collapsible
        defaultOpen={false}
        hint={`${filesShown.length.toLocaleString()} of ${touched.toLocaleString()} touched`}
        description={`The files with the most commits attributed to this person. Churn is a percentile against every file in the repo. A dot marks a high-churn file they alone maintain and wrote at least 90% of the commits for${soleCarried > 0 ? `: ${soleCarried} of these ${filesShown.length}` : ", and none of these qualify"}.`}
      >
        <OwnerFileTable files={filesShown} hrefForFile={hrefForFile} LinkComponent={LinkComponent} />
      </OverviewSection>

      <OverviewSection title="What they commit" collapsible defaultOpen={false}>
        <OwnerCommitMix categories={owner.commit_categories} />
      </OverviewSection>
    </div>
  );
}

function OwnerCreditSection({
  credit,
  LinkComponent,
}: {
  credit: OwnerCredit;
  LinkComponent?: React.ElementType | undefined;
}) {
  if (credit.filesImproved === 0 && credit.findingsResolved === 0) return null;
  return (
    <OverviewSection
      title="What they improved"
      {...(credit.href
        ? {
            action: (
              <SectionLink href={credit.href} LinkComponent={LinkComponent}>
                See the commits
              </SectionLink>
            ),
          }
        : {})}
    >
      <p className="text-[15px] leading-relaxed text-[var(--color-text-secondary)]">
        In the last {credit.windowDays} days their commits made{" "}
        <strong className="font-semibold text-[var(--color-text-primary)]">
          {credit.filesImproved.toLocaleString()} {credit.filesImproved === 1 ? "file" : "files"}
        </strong>{" "}
        healthier and resolved{" "}
        <strong className="font-semibold text-[var(--color-text-primary)]">
          {credit.findingsResolved.toLocaleString()} {credit.findingsResolved === 1 ? "finding" : "findings"}
        </strong>
        .
      </p>
    </OverviewSection>
  );
}
