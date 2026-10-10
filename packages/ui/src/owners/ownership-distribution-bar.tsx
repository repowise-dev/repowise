import * as React from "react";
import type { OwnerListEntry } from "@repowise-dev/types/owners";
import { ProportionBar, SHARE_STEPS } from "../shared/proportion-bar";

interface OwnershipDistributionBarProps {
  owners: OwnerListEntry[];
  /** Repo-wide contributor count (may exceed the loaded `owners`). */
  totalContributors: number;
  onSelect?: (owner: OwnerListEntry) => void;
  /** Router link for the drill-in; defaults to a plain anchor. */
  hrefFor?: (owner: OwnerListEntry) => string;
  LinkComponent?: React.ElementType | undefined;
}

/**
 * How owned files spread across contributors, as one proportional bar.
 *
 * The point is the shape: a bar dominated by one or two segments is a
 * concentration risk, an even spread is healthy. Legend entries drill into
 * the owner profile.
 *
 * A server component. It takes `hrefFor` as well as `onSelect`, so a
 * server-rendered page links and only a page that genuinely needs the
 * callback pays for hydration.
 */
export function OwnershipDistributionBar({
  owners,
  totalContributors,
  onSelect,
  hrefFor,
  LinkComponent,
}: OwnershipDistributionBarProps) {
  // The fold names every contributor past the shown ones, not only the
  // loaded page of them.
  const others = Math.max(0, totalContributors - SHARE_STEPS.length);
  return (
    <ProportionBar
      label="Owned files by contributor"
      segments={owners.map((o) => ({
        key: o.key,
        label: o.name || o.email || "unknown",
        value: o.files_owned,
        ...(hrefFor
          ? { href: hrefFor(o) }
          : onSelect
            ? { onSelect: () => onSelect(o) }
            : {}),
      }))}
      othersLabel={(n) => `${Math.max(n, others).toLocaleString()} others`}
      LinkComponent={LinkComponent}
    />
  );
}
