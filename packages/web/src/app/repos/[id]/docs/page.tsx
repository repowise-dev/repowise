"use client";

import { use } from "react";
import { DocsExplorer } from "@/components/docs/docs-explorer";
import { DOCS_READER_SHELL_CLASS } from "./docs-reader-shell";

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
    // Fill the height `main` actually leaves, not the whole viewport — see
    // docs-reader-shell.ts for why that has to be a definite height.
    <div className={DOCS_READER_SHELL_CLASS}>
      <DocsExplorer repoId={repoId} />
    </div>
  );
}
