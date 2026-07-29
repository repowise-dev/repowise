"use client";

import { use } from "react";
import { DocsExplorer } from "@/components/docs/docs-explorer";

// Thin shell — the DocsHeader, search palette, export menu, and per-page
// controls all live in DocsExplorer, which owns the page selection and
// reader-level state they depend on.
export default function DocsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: repoId } = use(params);

  return (
    // Fill the height `main` actually leaves, not the whole viewport. `main` is
    // already a flex child sitting below the banners a repo can stack above it
    // (reindex hint, active job, upgrade), so asking for `h-screen` here
    // overflowed by exactly the height of whichever ones were showing and
    // pushed the reader's footer chrome below the fold. `min-h-0` lets the tree
    // and reader scroll inside this box rather than growing it.
    <div className="flex flex-1 flex-col min-h-0">
      <DocsExplorer repoId={repoId} />
    </div>
  );
}
