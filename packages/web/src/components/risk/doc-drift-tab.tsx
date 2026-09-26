"use client";

/**
 * Doc drift host — binds the shared {@link DocDriftView} to web's `/api`
 * client and `/repos/:id` routing. The composition (lede, confidence split,
 * filters, findings table, and every refusal state) lives in
 * `@repowise-dev/ui/doc-drift`; this file only injects the app-specific
 * pieces, so web and hosted render the same view.
 */

import { useRouter } from "next/navigation";
import { DocDriftView, type DocDriftAdapter } from "@repowise-dev/ui/doc-drift";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import { getDocDrift, getDocDriftReferences } from "@/lib/api/doc-drift";

export function DocDriftTab({ repoId }: { repoId: string }) {
  const router = useRouter();
  const prefix = `/repos/${repoId}`;

  const adapter: DocDriftAdapter = {
    cacheKey: repoId,
    listFindings: (opts) => getDocDrift(repoId, opts),
    listReferences: (target) => getDocDriftReferences(repoId, target),
    // The line is dropped rather than appended: no file view renders `#L<n>`
    // anchors yet, and a fragment that resolves nowhere teaches the reader
    // that the link goes somewhere it does not.
    documentHref: (path) => fileEntityPath(prefix, path),
    navigate: (href) => router.push(href),
  };

  return <DocDriftView adapter={adapter} />;
}
