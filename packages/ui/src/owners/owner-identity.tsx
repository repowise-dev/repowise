import * as React from "react";
import type { OwnerProfile } from "@repowise-dev/types/owners";
import type { OwnershipLabels } from "./labels";

/** Active, quiet, or unknown when the indexed commits do not reach back far enough. */
export function ownerActivity(owner: Pick<OwnerProfile, "commit_count_90d" | "commit_count_90d_at_least">):
  | "active"
  | "quiet"
  | null {
  if (owner.commit_count_90d != null) return owner.commit_count_90d > 0 ? "active" : "quiet";
  return owner.commit_count_90d_at_least ? "active" : null;
}

/**
 * Who this is: whether they still commit, and every name and address the
 * index folded into them, so a reader looking for "jdoe@old-corp" finds the
 * person they know. Plain words, no badges: activity is a fact, not a grade.
 */
function activityLine(owner: OwnerProfile, labels: OwnershipLabels): string | null {
  const activity = ownerActivity(owner);
  if (activity !== "active") return activity && labels.quietLine;
  const exact = owner.commit_count_90d;
  return labels.activeLine(exact ?? owner.commit_count_90d_at_least ?? 0, exact == null);
}

/** Every folded-in name and address other than the ones the page already shows. */
function otherIdentities(owner: OwnerProfile): string[] {
  return [
    ...owner.aliases.names.filter((n) => n !== owner.name),
    ...owner.aliases.emails.filter((e) => e !== owner.email),
  ];
}

export function OwnerIdentity({ owner, labels }: { owner: OwnerProfile; labels: OwnershipLabels }) {
  const aliases = otherIdentities(owner);
  const rows = [
    { label: labels.activityLabel, hidden: true, value: activityLine(owner, labels), mono: false },
    { label: labels.emailLabel, hidden: false, value: owner.email !== owner.name ? owner.email : null, mono: true },
    { label: labels.aliasesLabel, hidden: false, value: aliases.join(", ") || null, mono: true },
  ].filter((r) => r.value);

  if (rows.length === 0) return null;
  return (
    <dl className="flex flex-col gap-1.5 text-xs text-[var(--color-text-secondary)]">
      {rows.map((r) => (
        <div key={r.label} className="flex flex-wrap gap-x-2">
          <dt className={r.hidden ? "sr-only" : "text-[var(--color-text-tertiary)]"}>{r.label}</dt>
          <dd className={r.mono ? "font-mono [overflow-wrap:anywhere]" : undefined}>{r.value}</dd>
        </div>
      ))}
    </dl>
  );
}
