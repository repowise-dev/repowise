"use client";

import { useState, useCallback, useEffect } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { BookOpen, PanelLeftClose, PanelLeft } from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { usePages } from "@/lib/hooks/use-pages";
import { DocsTree } from "@repowise-dev/ui/docs/docs-tree";
import { DocsViewer } from "./docs-viewer";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
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
  const searchParams = useSearchParams();
  const router = useRouter();

  // Restore selection from URL on mount / when pages load
  useEffect(() => {
    if (pages.length === 0) return;
    const pageId = searchParams.get("page");
    if (pageId && !selectedPage) {
      const match = pages.find((p) => p.id === pageId);
      if (match) setSelectedPage(match);
    }
  }, [pages, searchParams, selectedPage]);

  const handleSelectPage = useCallback((page: PageResponse) => {
    setSelectedPage(page);
    // Update URL without full navigation
    const params = new URLSearchParams(searchParams.toString());
    params.set("page", page.id);
    router.replace(`?${params.toString()}`, { scroll: false });
  }, [searchParams, router]);

  if (isLoading) {
    return (
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
  }

  if (pages.length === 0) {
    return (
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
  }

  return (
    <div className="flex h-full">
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
        <DocsViewer page={selectedPage} repoId={repoId} />
      </div>
    </div>
  );
}
