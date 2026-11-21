"use client";

/** Overview's Repo report card: the living report's progress and top three, loaded after the page. */

import Link from "next/link";
import useSWR from "swr";
import { RepoReportCard } from "@repowise-dev/ui/report/first-look";
import { getFirstLook } from "@/lib/api/reports";
import { useFirstLookCopy } from "./first-look-copy";
import { firstLookKey } from "./first-look-view";

export function RepoReportCardHost({ repoId }: { repoId: string }) {
  const copy = useFirstLookCopy();
  // Shares the page's cache entry, so opening the report after the card costs nothing.
  const { data } = useSWR(firstLookKey(repoId), () => getFirstLook(repoId, { flavor: "generic" }));
  if (!data) return null;
  return (
    <div className="mb-6">
      <RepoReportCard report={data.report} href={`/repos/${repoId}/report`} copy={copy} LinkComponent={Link} />
    </div>
  );
}
