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
    <div className="flex flex-col h-screen">
      <DocsExplorer repoId={repoId} />
    </div>
  );
}
