"use client";

import { useState, useCallback, useEffect, useMemo } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { BookOpen, PanelLeftClose, PanelLeft, Search } from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { usePages } from "@/lib/hooks/use-pages";
import { DocsTree } from "@repowise-dev/ui/docs/docs-tree";
import { DocsCommandPalette } from "@repowise-dev/ui/docs/command-palette";
import {
  DEFAULT_PERSONA,
  type ReaderPersona,
  isReaderPersona,
  personaFilteringApplies,
} from "@repowise-dev/ui/docs/reader-persona";
import { DocsHeader } from "./docs-header";
import { DocsViewer } from "./docs-viewer";
import {
  DocsPageActions,
  ExportMenu,
  SidebarToggle,
} from "./docs-page-actions";
import { search as searchPages } from "@/lib/api/search";
import { downloadTextFile } from "@/lib/utils/download";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import type { DocPage } from "@repowise-dev/types/docs";
import type { PageResponse } from "@/lib/api/types";

interface DocsExplorerProps {
  repoId: string;
}

export function DocsExplorer({ repoId }: DocsExplorerProps) {
  const { pages, isLoading } = usePages(repoId);
  const [selectedPage, setSelectedPage] = useState<PageResponse | null>(null);
  const [treePanelOpen, setTreePanelOpen] = useState(() => {
    if (typeof window === "undefined") return true;
    return window.matchMedia("(min-width: 768px)").matches;
  });
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [searchOpen, setSearchOpen] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const searchParams = useSearchParams();
  const router = useRouter();

  // Reader persona — a client-side section filter, persisted in the URL
  // (?reader=) so a chosen depth is shareable and survives navigation. Owned
  // here (not in the viewer) because the control renders in the DocsHeader.
  const readerParam = searchParams.get("reader");
  const persona: ReaderPersona = isReaderPersona(readerParam) ? readerParam : DEFAULT_PERSONA;
  const setPersona = useCallback(
    (next: ReaderPersona) => {
      const params = new URLSearchParams(searchParams.toString());
      if (next === DEFAULT_PERSONA) params.delete("reader");
      else params.set("reader", next);
      const qs = params.toString();
      router.replace(qs ? `?${qs}` : "?", { scroll: false });
    },
    [router, searchParams],
  );
  // The reader control only renders when filtering would change this page —
  // on curated pages (guided tour, overviews) it's a no-op, so it hides.
  const personaHasEffect = useMemo(
    () => (selectedPage ? personaFilteringApplies(selectedPage.content) : false),
    [selectedPage],
  );

  // Keep the selected page in sync with the ?page= URL param. This fires on
  // mount and whenever the param changes — including when an in-content wiki
  // link or breadcrumb navigates via <Link href="?page=...">.
  const pageParam = searchParams.get("page");
  useEffect(() => {
    if (pages.length === 0) return;
    if (pageParam) {
      if (selectedPage?.id === pageParam) return;
      const match = pages.find((p) => p.id === pageParam);
      if (match) setSelectedPage(match);
      return;
    }
    // No ?page= in the URL — open the repo overview by default (falling back
    // to the first page) so the viewer never lands on an empty state.
    if (selectedPage) return;
    const overview = pages.find((p) => p.page_type === "repo_overview");
    setSelectedPage(overview ?? pages[0]);
  }, [pages, pageParam, selectedPage]);

  const handleSelectPage = useCallback((page: PageResponse) => {
    setSelectedPage(page);
    // Update URL without full navigation
    const params = new URLSearchParams(searchParams.toString());
    params.set("page", page.id);
    router.replace(`?${params.toString()}`, { scroll: false });
  }, [searchParams, router]);

  // Server-backed search for the ⌘K palette: hit the semantic/full-text
  // endpoint, then map results back to the loaded page objects so selection
  // behaves identically to a client-side hit. Unknown ids (rare) are dropped.
  const searchFn = useCallback(
    async (q: string) => {
      const results = await searchPages(q, { repo_id: repoId, limit: 30 });
      const byId = new Map(pages.map((p) => [p.id, p]));
      return results
        .map((r) => byId.get(r.page_id))
        .filter((p): p is PageResponse => p !== undefined) as unknown as DocPage[];
    },
    [repoId, pages],
  );

  const handleExportAll = useCallback(() => {
    setIsExporting(true);
    try {
      const sorted = [...pages].sort((a, b) =>
        a.target_path.localeCompare(b.target_path),
      );
      const content = sorted
        .map((p) => `# ${p.title}\n\n> ${p.target_path}\n\n${p.content}`)
        .join("\n\n---\n\n");
      downloadTextFile(content, "documentation-export.md");
    } finally {
      setIsExporting(false);
    }
  }, [pages]);

  let body: React.ReactNode;
  if (isLoading) {
    body = (
      <div className="flex h-full">
        <div className="w-full md:w-[300px] border-r border-[var(--color-border-default)] p-3 space-y-2">
          <Skeleton className="h-8 w-full rounded-md" />
          <Skeleton className="h-4 w-3/4 rounded" />
          <Skeleton className="h-4 w-1/2 rounded" />
          <Skeleton className="h-4 w-5/6 rounded" />
          <Skeleton className="h-4 w-2/3 rounded" />
          <Skeleton className="h-4 w-3/4 rounded" />
          <Skeleton className="h-4 w-1/2 rounded" />
        </div>
        <div className="flex-1 flex items-center justify-center">
          <Skeleton className="h-8 w-48 rounded" />
        </div>
      </div>
    );
  } else if (pages.length === 0) {
    body = (
      <div className="flex flex-col items-center justify-center h-full gap-4 text-center px-8">
        <div className="rounded-full bg-[var(--color-bg-elevated)] border border-[var(--color-border-default)] p-4">
          <BookOpen className="h-8 w-8 text-[var(--color-text-tertiary)]" />
        </div>
        <div className="space-y-1">
          <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
            No documentation yet
          </h3>
          <p className="text-xs text-[var(--color-text-secondary)] max-w-sm">
            Run a generation job to create AI-powered documentation for this codebase.
          </p>
        </div>
      </div>
    );
  } else {
    body = (
      <div className="relative flex h-full">
        {/* Tree sidebar */}
        <div
          className={cn(
            "border-r border-[var(--color-border-default)] bg-[var(--color-bg-surface)] transition-all duration-200 shrink-0 relative",
            treePanelOpen ? "w-full md:w-[300px]" : "w-0 overflow-hidden border-r-0",
          )}
        >
          <DocsTree
            pages={pages}
            selectedPageId={selectedPage?.id ?? null}
            onSelectPage={(p) => {
              handleSelectPage(p);
              if (typeof window !== "undefined" && !window.matchMedia("(min-width: 768px)").matches) {
                setTreePanelOpen(false);
              }
            }}
          />
        </div>

        {/* Toggle button */}
        <button
          onClick={() => setTreePanelOpen((o) => !o)}
          aria-label={treePanelOpen ? "Hide pages tree" : "Show pages tree"}
          aria-expanded={treePanelOpen}
          className={cn(
            "absolute top-3 z-20 rounded-r-md border border-l-0 border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-1 text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-bg-elevated)] transition-colors",
            treePanelOpen ? "hidden md:block left-[300px]" : "left-0",
          )}
        >
          {treePanelOpen ? (
            <PanelLeftClose className="h-3.5 w-3.5" />
          ) : (
            <PanelLeft className="h-3.5 w-3.5" />
          )}
        </button>

        {/* Viewer */}
        <div className={cn("flex-1 min-w-0", treePanelOpen ? "hidden md:block" : "block")}>
          <DocsViewer
            page={selectedPage}
            pages={pages}
            repoId={repoId}
            onSelectPage={handleSelectPage}
            persona={persona}
            sidebarOpen={sidebarOpen}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <DocsHeader>
        {selectedPage && (
          <DocsPageActions
            page={selectedPage}
            persona={persona}
            setPersona={setPersona}
            personaHasEffect={personaHasEffect}
          />
        )}
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
          onExportAll={handleExportAll}
          zipHref={`/api/repos/${repoId}/export`}
          page={selectedPage}
          repoId={repoId}
        />
        {selectedPage && (
          <SidebarToggle
            open={sidebarOpen}
            onToggle={() => setSidebarOpen((o) => !o)}
          />
        )}
      </DocsHeader>

      <div className="flex-1 min-h-0">{body}</div>

      {/* ⌘K full-text command palette over loaded pages */}
      <DocsCommandPalette
        pages={pages as unknown as DocPage[]}
        open={searchOpen}
        onOpenChange={setSearchOpen}
        onSelect={(p) => handleSelectPage(p as unknown as PageResponse)}
        searchFn={searchFn}
      />
    </div>
  );
}
