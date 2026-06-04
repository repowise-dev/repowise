"use client";

import { use, useCallback, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import useSWR from "swr";
import {
  ChevronDown,
  Download,
  FileText,
  FolderArchive,
  Loader2,
  Search,
} from "lucide-react";
import { Button } from "@repowise-dev/ui/ui/button";
import { DocsCommandPalette } from "@repowise-dev/ui/docs/command-palette";
import { DocsHeader } from "@/components/docs/docs-header";
import { DocsExplorer } from "@/components/docs/docs-explorer";
import { listAllPages } from "@/lib/api/pages";
import { search as searchPages } from "@/lib/api/search";
import type { DocPage } from "@repowise-dev/types/docs";
import { downloadTextFile } from "@/lib/utils/download";

/** Single Export action — markdown bundle or ZIP, behind one menu. */
function ExportMenu({
  isExporting,
  onExportMarkdown,
  zipHref,
}: {
  isExporting: boolean;
  onExportMarkdown: () => void;
  zipHref: string;
}) {
  const [open, setOpen] = useState(false);
  const itemClass =
    "flex w-full items-center gap-2 rounded px-2 py-1.5 text-xs text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]";

  return (
    <div className="relative">
      <Button
        variant="outline"
        size="sm"
        onClick={() => setOpen((o) => !o)}
        disabled={isExporting}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        {isExporting ? (
          <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
        ) : (
          <Download className="h-3.5 w-3.5 mr-1.5" />
        )}
        Export
        <ChevronDown className="h-3 w-3 ml-1 opacity-60" />
      </Button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div
            role="menu"
            className="absolute right-0 top-full z-20 mt-1 w-48 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-1 shadow-md"
          >
            <button
              role="menuitem"
              className={itemClass}
              onClick={() => {
                setOpen(false);
                onExportMarkdown();
              }}
            >
              <FileText className="h-3.5 w-3.5 shrink-0" />
              Single Markdown file
            </button>
            <a
              role="menuitem"
              href={zipHref}
              download
              className={itemClass}
              onClick={() => setOpen(false)}
            >
              <FolderArchive className="h-3.5 w-3.5 shrink-0" />
              ZIP archive
            </a>
          </div>
        </>
      )}
    </div>
  );
}

export default function DocsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: repoId } = use(params);
  const [isExporting, setIsExporting] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const router = useRouter();
  const searchParams = useSearchParams();

  const openPage = useCallback(
    (pageId: string) => {
      const next = new URLSearchParams(searchParams.toString());
      next.set("page", pageId);
      router.replace(`?${next.toString()}`, { scroll: false });
    },
    [router, searchParams],
  );

  // Share the exact SWR key the explorer's `usePages` uses so the (large) full
  // page list — content included — is fetched once and deduped, not twice.
  const { data: docPages } = useSWR<DocPage[]>(
    `pages:${repoId}:all`,
    () => listAllPages(repoId) as Promise<DocPage[]>,
    { revalidateOnFocus: false },
  );

  // Server-backed search for the ⌘K palette: hit the semantic/full-text
  // endpoint, then map results back to the loaded page objects so selection
  // behaves identically to a client-side hit. Unknown ids (rare) are dropped.
  const searchFn = useCallback(
    async (q: string) => {
      const results = await searchPages(q, { repo_id: repoId, limit: 30 });
      const byId = new Map((docPages ?? []).map((p) => [p.id, p]));
      return results
        .map((r) => byId.get(r.page_id))
        .filter((p): p is DocPage => p !== undefined);
    },
    [repoId, docPages],
  );

  const handleExportAll = async () => {
    setIsExporting(true);
    try {
      const pages = await listAllPages(repoId);
      pages.sort((a, b) => a.target_path.localeCompare(b.target_path));
      const content = pages
        .map((p) => `# ${p.title}\n\n> ${p.target_path}\n\n${p.content}`)
        .join("\n\n---\n\n");
      downloadTextFile(content, "documentation-export.md");
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <div className="flex flex-col h-screen">
      <DocsHeader>
        <button
          onClick={() => setSearchOpen(true)}
          className="flex items-center gap-2 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2.5 py-1.5 text-xs text-[var(--color-text-tertiary)] transition-colors hover:text-[var(--color-text-secondary)]"
        >
          <Search className="h-3.5 w-3.5" />
          <span className="hidden sm:inline">Search</span>
          <kbd className="hidden rounded border border-[var(--color-border-default)] px-1 py-0.5 text-[10px] sm:inline">
            ⌘K
          </kbd>
        </button>
        <ExportMenu
          isExporting={isExporting}
          onExportMarkdown={handleExportAll}
          zipHref={`/api/repos/${repoId}/export`}
        />
      </DocsHeader>

      {/* Explorer */}
      <div className="flex-1 min-h-0">
        <DocsExplorer repoId={repoId} />
      </div>

      {/* ⌘K full-text command palette over loaded pages */}
      <DocsCommandPalette
        pages={docPages ?? []}
        open={searchOpen}
        onOpenChange={setSearchOpen}
        onSelect={(p) => openPage(p.id)}
        searchFn={searchFn}
      />
    </div>
  );
}
