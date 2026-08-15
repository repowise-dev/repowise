"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useTransition } from "react";
import {
  RepoPairTable,
  type RepoPairSummary,
} from "@repowise-dev/ui/workspace/repo-pair-table";

/**
 * The repo-pair rollup, with selection kept in the URL.
 *
 * This replaces a `useState` drill-down that hid the file pairs behind a view
 * toggle and a Back button. Selecting a pair now narrows the section below
 * instead of navigating away from it, so both altitudes stay on screen and a
 * selected pair is a linkable address.
 */
export function RepoPairLinks({ repoPairs }: { repoPairs: RepoPairSummary[] }) {
  const router = useRouter();
  const params = useSearchParams();
  const [, startTransition] = useTransition();
  const selected = params.get("pair");

  return (
    <RepoPairTable
      repoPairs={repoPairs}
      selectedPairId={selected}
      onSelectPair={(id) => {
        const next = new URLSearchParams(params.toString());
        // Clicking the selected pair clears it, so the control can undo itself.
        if (id === selected) next.delete("pair");
        else next.set("pair", id);
        startTransition(() => {
          router.push(
            next.size > 0 ? `?${next.toString()}` : "/workspace/co-changes",
            { scroll: false },
          );
        });
      }}
    />
  );
}
