"use client";

import Link from "next/link";
import type { OrphanProvider, UnmatchedConsumer } from "@repowise-dev/types/workspace";
import type { OrphanGroup } from "@repowise-dev/ui/workspace/contract-facts";
import { ContractAttention } from "@repowise-dev/ui/workspace/contract-attention";
import { useOpenContract } from "./contract-drawer-host";
import { contractsListHref } from "./contract-href";

/** The attention lists, with rows that open the drawer and summaries that filter the list. */
export function NeedsAttention({
  unmatched,
  orphanGroups,
}: {
  unmatched: UnmatchedConsumer[];
  orphanGroups: OrphanGroup<OrphanProvider>[];
}) {
  const open = useOpenContract();
  return (
    <ContractAttention
      unmatched={unmatched}
      orphanGroups={orphanGroups}
      onSelect={open}
      browseHref={(repo, type) =>
        contractsListHref({ repo, type, role: "provider", linked: "no" })
      }
      LinkComponent={Link}
    />
  );
}
