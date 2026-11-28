import * as React from "react";
import type { OwnerCoAuthor } from "@repowise-dev/types/owners";
import { OverviewSection } from "../overview/section";
import { EmptyState } from "../shared/empty-state";
import type { OwnershipLabels } from "./labels";
import { OwnerAvatar } from "./owner-avatar";

/**
 * Who else works in the same files.
 *
 * Overlap on a real repo is one partner and then a cliff — 15.8%, then 1.6%,
 * 1.2%, 1.0% — so a list of ten presents people who once touched the same
 * file at the weight of the person who actually shares the work. The lead
 * partner answers the question the section exists for (who reviews this, who
 * picks it up), and the tail is reported as a count.
 */
export function OwnerRelationships({
  coAuthors,
  total,
  hrefForCoAuthor,
  LinkComponent,
  labels,
}: {
  coAuthors: OwnerCoAuthor[];
  total: number;
  hrefForCoAuthor?: ((coAuthor: OwnerCoAuthor) => string) | undefined;
  LinkComponent?: React.ElementType | undefined;
  labels: OwnershipLabels;
}) {
  const A = LinkComponent ?? "a";
  const [lead, second] = [...coAuthors].sort(
    (a, b) => b.co_change_strength - a.co_change_strength,
  );

  return (
    <OverviewSection
      title={labels.relationshipsTitle}
      description={labels.relationshipsDescription}
    >
      {!lead ? (
        <EmptyState
          className="p-6"
          title={labels.relationshipsEmptyTitle}
          description={labels.relationshipsEmptyDescription}
        />
      ) : (
        <div className="flex flex-col gap-3">
          <LeadPartner coAuthor={lead} href={hrefForCoAuthor?.(lead)} LinkComponent={A} labels={labels} />
          <p className="max-w-[62ch] text-xs leading-relaxed text-[var(--color-text-tertiary)] [text-wrap:pretty]">
            {total <= 1
              ? labels.relationshipsAlone
              : second
                ? labels.relationshipsNext(total - 1, (second.co_change_strength * 100).toFixed(1))
                : labels.relationshipsOthers(total - 1)}
          </p>
        </div>
      )}
    </OverviewSection>
  );
}

function LeadPartner({
  coAuthor,
  href,
  LinkComponent,
  labels,
}: {
  coAuthor: OwnerCoAuthor;
  href: string | undefined;
  LinkComponent: React.ElementType;
  labels: OwnershipLabels;
}) {
  const A = LinkComponent;
  const body = (
    <>
      <OwnerAvatar name={coAuthor.name} email={coAuthor.email} size="md" />
      <span className="min-w-0">
        <span className="block font-medium text-[var(--color-text-primary)] transition-colors group-hover:text-[var(--color-accent-primary)] [text-wrap:pretty]">
          {coAuthor.name || coAuthor.email || labels.unknownName}
        </span>
        <span className="block text-xs tabular-nums text-[var(--color-text-tertiary)]">
          {labels.partnerLine(coAuthor.shared_files, (coAuthor.co_change_strength * 100).toFixed(1))}
        </span>
      </span>
    </>
  );

  return href ? (
    <A href={href} className="group flex w-fit items-center gap-3 no-underline">
      {body}
    </A>
  ) : (
    <span className="flex items-center gap-3">{body}</span>
  );
}
