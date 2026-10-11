"use client";

/**
 * The Doc tab's "Documents naming this file" list, bound to web's `/api`
 * client and `/repos/:id` routing.
 *
 * Client-side for the same reason as `FileTestsPanel`: the shared list fetches,
 * and a fetcher cannot be handed to a server-rendered tab body as a prop.
 */

import { DocDriftReferences } from "@repowise-dev/ui/doc-drift";
import { docDriftHref } from "@repowise-dev/ui/dashboard/attention-href";
import { useDocDriftAdapter } from "@/components/risk/doc-drift-tab";

export function FileDocReferencesPanel({
  repoId,
  filePath,
}: {
  repoId: string;
  filePath: string;
}) {
  const adapter = useDocDriftAdapter(repoId);
  return (
    <DocDriftReferences
      target={filePath}
      adapter={adapter}
      driftHref={(document) => docDriftHref(`/repos/${repoId}`, document)}
    />
  );
}
