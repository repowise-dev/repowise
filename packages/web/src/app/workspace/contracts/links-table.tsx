"use client";

import { useCallback } from "react";
import useSWR from "swr";
import { OverviewSection } from "@repowise-dev/ui/overview/section";
import { ContractLinksTable } from "@repowise-dev/ui/workspace/contract-links-table";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import type { WorkspaceContractLinkEntry } from "@/lib/api/types";
import { getWorkspaceContracts } from "@/lib/api/workspace";
import { useOpenContract } from "./contract-drawer-host";

/**
 * The matched links, fetched here rather than on the server page.
 *
 * The page re-renders on every page turn and search, and carrying all links in
 * that payload resent them each time. Keyed only on the filters that narrow
 * links (type, repo, search), so paging and the role or link filters reuse the
 * cached answer. A row opens the provider side in the drawer: its callers list
 * this link among the rest, and each caller swaps the drawer to itself.
 */
export function LinksSection({
  type,
  repo,
  q,
}: {
  type?: string | undefined;
  repo?: string | undefined;
  q?: string | undefined;
}) {
  const open = useOpenContract();
  const { data, error, isLoading } = useSWR(
    ["workspace:contract-links", type ?? "", repo ?? "", q ?? ""],
    // limit=1: the endpoint pages contracts, not links, so this is the cheapest
    // request that returns every matching link.
    () => getWorkspaceContracts({ contract_type: type, repo, q, limit: 1 }),
    { revalidateOnFocus: false },
  );
  const onSelect = useCallback(
    (l: WorkspaceContractLinkEntry) =>
      open({ repo: l.provider_repo, file_path: l.provider_file, contract_id: l.contract_id }),
    [open],
  );

  const links = data?.links ?? [];
  if (!isLoading && !error && links.length === 0) return null;
  const filtered = Boolean(type || repo || q);

  return (
    <OverviewSection
      title="Matched links"
      {...(data ? { description: linksDescription(links, filtered) } : {})}
    >
      {error ? (
        <p className="text-xs text-[var(--color-text-secondary)]">
          The matched links could not be loaded. Reload to try again.
        </p>
      ) : !data ? (
        <p className="text-xs text-[var(--color-text-secondary)]">Loading matched links...</p>
      ) : (
        <ContractLinksTable links={links} onSelect={onSelect} />
      )}
    </OverviewSection>
  );
}

/** Name the match basis once, since it is the same word on nearly every row. */
function linksDescription(links: { match_type: string }[], filtered: boolean): string {
  const byType = new Map<string, number>();
  for (const l of links) byType.set(l.match_type, (byType.get(l.match_type) ?? 0) + 1);
  const basis =
    byType.size === 1 && byType.has("exact")
      ? `All ${formatNumber(links.length)} are exact matches.`
      : [...byType.entries()].map(([t, n]) => `${formatNumber(n)} ${t}`).join(", ") + ".";
  const head = filtered
    ? `${formatNumber(links.length)} ${links.length === 1 ? "link matches" : "links match"} the type, repository and search.`
    : "Every provider joined to the consumer that calls it, strongest first.";
  return `${head} ${basis} Open a row for the provider and every caller.`;
}
