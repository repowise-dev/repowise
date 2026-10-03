"use client";

/**
 * Doc drift host — binds the shared {@link DocDriftView} to web's `/api`
 * client and `/repos/:id` routing. The composition (lede, confidence split,
 * filters, findings table, and every refusal state) lives in
 * `@repowise-dev/ui/doc-drift`; this file only injects the app-specific
 * pieces, so web and hosted render the same view.
 */

import { useMemo } from "react";
import { useRouter } from "next/navigation";
import { DocDriftView, type DocDriftAdapter } from "@repowise-dev/ui/doc-drift";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import { getDocDrift, getDocDriftReferences } from "@/lib/api/doc-drift";

/** Web's adapter, shared by the drift tab and the file page's references list. */
export function useDocDriftAdapter(repoId: string): DocDriftAdapter {
  const router = useRouter();
  return useMemo(() => {
    const prefix = `/repos/${repoId}`;
    return {
      cacheKey: repoId,
      listFindings: (opts) => getDocDrift(repoId, opts),
      listReferences: (target) => getDocDriftReferences(repoId, target),
      // The line is dropped rather than appended: no file view renders `#L<n>`
      // anchors yet, and a fragment that resolves nowhere teaches the reader
      // that the link goes somewhere it does not.
      documentHref: (path) => fileEntityPath(prefix, path),
      navigate: (href) => router.push(href),
    };
  }, [repoId, router]);
}

export function DocDriftTab({
  repoId,
  initialDocument,
}: {
  repoId: string;
  /** From `?document=` on the code-health page. */
  initialDocument?: string | undefined;
}) {
  const adapter = useDocDriftAdapter(repoId);
  return (
    <DocDriftView
      // Remount on a new deep link: the view reads it once, as initial state.
      key={initialDocument ?? ""}
      adapter={adapter}
      initialDocument={initialDocument}
    />
  );
}
