"use client";

/**
 * Impact host: binds the shared {@link ImpactView} to web's `/api` client and
 * keeps the picked change in the URL. The composition lives in
 * `@repowise-dev/ui/blast-radius`; this file only injects the app-specific
 * pieces so web and hosted render the same view.
 */

import { useMemo } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ImpactView, type ImpactAdapter } from "@repowise-dev/ui/blast-radius";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import {
  analyzeBlastRadius,
  analyzeChangeBlastRadius,
  checkChangeHealth,
  listBlastRadiusChanges,
} from "@/lib/api/blast-radius";
import { getHotspots } from "@/lib/api/git";
import { searchNodes } from "@/lib/api/graph";

export function ImpactTab({ repoId }: { repoId: string }) {
  // `?file=` is how every route into this view arrives: the file card's "Blast
  // Radius" button and the symbol drawer's CTA both link here with the path
  // already chosen. Nothing read the param before, so those links landed on an
  // empty picker and asked for the path back.
  const searchParams = useSearchParams();
  const seeded = searchParams.getAll("file").filter(Boolean);

  const adapter: ImpactAdapter = useMemo(() => ({
    cacheKey: repoId,
    listHotspots: (limit) => getHotspots(repoId, limit),
    searchFiles: async (q) => {
      const results = await searchNodes(repoId, q, 8);
      return results.map((r) => r.node_id);
    },
    analyze: ({ changedFiles, maxDepth }) =>
      analyzeBlastRadius(repoId, { changed_files: changedFiles, max_depth: maxDepth, with_graph: true }),
    listChanges: () => listBlastRadiusChanges(repoId),
    analyzeChange: ({ changeId, maxDepth }) =>
      analyzeChangeBlastRadius(repoId, { change_id: changeId, max_depth: maxDepth }),
    checkHealth: (changeId) => checkChangeHealth(repoId, changeId),
    fileHref: (path) => fileEntityPath(`/repos/${repoId}`, path),
  }), [repoId]);

  // `?change=` (wip, a branch, a full sha) makes a review shareable: picking a
  // change writes it, opening the link opens that change.
  const router = useRouter();
  const pathname = usePathname();
  const onChangePicked = (change: string) => {
    const next = new URLSearchParams(searchParams.toString());
    next.delete("file");
    next.set("change", change);
    router.replace(`${pathname}?${next.toString()}`, { scroll: false });
  };

  return (
    <ImpactView
      adapter={adapter}
      {...(seeded.length > 0 ? { initialFiles: seeded } : {})}
      initialChange={searchParams.get("change") ?? undefined}
      onChangePicked={onChangePicked}
    />
  );
}
