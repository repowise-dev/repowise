"use client";

import Link from "next/link";
import useSWR from "swr";
import { ReviewNudge } from "@repowise-dev/ui/blast-radius";
import { listBlastRadiusChanges } from "@/lib/api/blast-radius";
import { reviewHref } from "@/lib/changes";

/** The Overview's "Your change" line: the candidate list only, fetched after paint, silent on failure. */
export function ReviewNudgeHost({ repoId }: { repoId: string }) {
  const { data } = useSWR(`blast-radius-changes:${repoId}`, () => listBlastRadiusChanges(repoId).catch(() => null), {
    revalidateOnFocus: false,
  });
  return <ReviewNudge data={data} href={(change) => reviewHref(repoId, { change })} LinkComponent={Link} />;
}
