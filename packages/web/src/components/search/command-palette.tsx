"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Command } from "cmdk";
import useSWR from "swr";
import { Search, LayoutDashboard, Settings, BookOpen, FileCode, Layers, Link2, GitMerge, MessageSquare } from "lucide-react";
import { useSearch } from "@/lib/hooks/use-search";
import { truncatePath } from "@repowise-dev/ui/lib/format";
import { getFilesIndex } from "@/lib/api/files";
import { repoNavItems } from "@/components/layout/nav-items";
import { pageHref } from "@/lib/utils/page-href";
import type { RepoResponse, WorkspaceResponse } from "@/lib/api/types";

interface CommandPaletteProps {
  repos: RepoResponse[];
  workspace?: WorkspaceResponse | null;
}

export function CommandPalette({ repos, workspace }: CommandPaletteProps) {
  const isWorkspace = workspace?.is_workspace ?? false;
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const router = useRouter();
  const pathname = usePathname();

  const { results, isLoading } = useSearch(query, { limit: 8 });

  // Active repo: from the URL when inside one, else the only repo.
  const activeRepo = useMemo(() => {
    const m = pathname?.match(/^\/repos\/([^/]+)/);
    const fromPath = m ? repos.find((r) => r.id === m[1]) : undefined;
    return fromPath ?? (repos.length === 1 ? repos[0] : undefined);
  }, [pathname, repos]);

  const repoPages = useMemo(
    () => (activeRepo ? repoNavItems(activeRepo.id) : []),
    [activeRepo],
  );

  // File jump — fetched lazily (only once the palette is open with a repo in
  // scope) and cached; the Files page shares the same SWR key so this is warm
  // after a visit there. We do our own ranking and cap the rendered set so the
  // palette never mounts thousands of cmdk items.
  const { data: filesData } = useSWR(
    open && activeRepo ? `files-index:${activeRepo.id}` : null,
    () => getFilesIndex(activeRepo!.id),
    { revalidateOnFocus: false },
  );

  const fileMatches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!activeRepo || q.length < 2 || !filesData) return [];
    const scored: { path: string; score: number }[] = [];
    for (const f of filesData.files) {
      const path = f.file_path.toLowerCase();
      const idx = path.indexOf(q);
      if (idx === -1) continue;
      const base = f.file_path.split("/").pop()?.toLowerCase() ?? "";
      // Rank: basename match beats mid-path; earlier match beats later.
      const score = (base.includes(q) ? 0 : 1000) + idx;
      scored.push({ path: f.file_path, score });
    }
    scored.sort((a, b) => a.score - b.score || a.path.length - b.path.length);
    return scored.slice(0, 12).map((s) => s.path);
  }, [query, activeRepo, filesData]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    const openHandler = () => setOpen(true);
    window.addEventListener("keydown", handler);
    window.addEventListener("repowise:open-command-palette", openHandler);
    return () => {
      window.removeEventListener("keydown", handler);
      window.removeEventListener("repowise:open-command-palette", openHandler);
    };
  }, []);

  const navigate = useCallback(
    (href: string) => {
      router.push(href);
      setOpen(false);
      setQuery("");
    },
    [router],
  );

  return (
    <Command.Dialog
      open={open}
      onOpenChange={setOpen}
      label="Command palette"
      className="fixed inset-0 z-[calc(var(--z-modal)+1)] flex items-start justify-center pt-[10vh] sm:pt-[20vh] px-4"
    >
      <div
        className="fixed inset-0 bg-black/60 backdrop-blur-sm"
        onClick={() => setOpen(false)}
      />
      <div className="relative z-10 w-full max-w-xl rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] shadow-2xl overflow-hidden">
        <div className="flex items-center gap-3 px-4 py-3 border-b border-[var(--color-border-default)]">
          <Search className="h-4 w-4 text-[var(--color-text-tertiary)] shrink-0" />
          <Command.Input
            value={query}
            onValueChange={setQuery}
            placeholder="Jump to a file, search pages, navigate repos…"
            className="flex-1 bg-transparent text-sm text-[var(--color-text-primary)] outline-none placeholder:text-[var(--color-text-tertiary)]"
          />
          <kbd className="hidden sm:inline-flex items-center rounded border border-[var(--color-border-default)] px-1.5 py-0.5 text-xs text-[var(--color-text-tertiary)] font-mono">
            ESC
          </kbd>
        </div>

        <Command.List className="max-h-[60dvh] overflow-y-auto py-2">
          <Command.Empty className="px-4 py-8 text-center text-sm text-[var(--color-text-tertiary)]">
            {isLoading ? "Searching…" : "No results found."}
          </Command.Empty>

          {/* Quick-ask — always available when a repo is in scope */}
          {activeRepo && (
            <Command.Group heading="Ask" className="px-2 pb-1">
              <Command.Item
                value={`ask-repowise ${query}`}
                onSelect={() =>
                  navigate(
                    query.trim()
                      ? `/repos/${activeRepo.id}/chat?q=${encodeURIComponent(query.trim())}`
                      : `/repos/${activeRepo.id}/chat`,
                  )
                }
                className="flex items-center gap-2.5 rounded-md px-3 py-2 text-sm text-[var(--color-text-secondary)] cursor-pointer hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] data-[selected=true]:bg-[var(--color-bg-elevated)] data-[selected=true]:text-[var(--color-text-primary)]"
              >
                <MessageSquare className="h-4 w-4 text-[var(--color-accent-primary)]" />
                <span className="truncate">
                  {query.trim() ? (
                    <>
                      Ask repowise: <span className="text-[var(--color-text-primary)]">“{query.trim()}”</span>
                    </>
                  ) : (
                    "Ask repowise…"
                  )}
                </span>
              </Command.Item>
            </Command.Group>
          )}

          {/* Per-repo page navigation */}
          {activeRepo && repoPages.length > 0 && (
            <Command.Group heading={`Go to — ${activeRepo.name}`} className="px-2 pb-1">
              {repoPages.map((item) => {
                const Icon = item.icon;
                return (
                  <Command.Item
                    key={item.href}
                    value={`goto ${activeRepo.name} ${item.label}`}
                    onSelect={() => navigate(item.href)}
                    className="flex items-center gap-2.5 rounded-md px-3 py-2 text-sm text-[var(--color-text-secondary)] cursor-pointer hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] data-[selected=true]:bg-[var(--color-bg-elevated)] data-[selected=true]:text-[var(--color-text-primary)]"
                  >
                    <Icon className="h-4 w-4" />
                    {item.label}
                  </Command.Item>
                );
              })}
            </Command.Group>
          )}

          {/* Quick navigation */}
          <Command.Group
            heading="Navigate"
            className="px-2 pb-1"
          >
            <Command.Item
              value="dashboard"
              onSelect={() => navigate("/")}
              className="flex items-center gap-2.5 rounded-md px-3 py-2 text-sm text-[var(--color-text-secondary)] cursor-pointer hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] data-[selected=true]:bg-[var(--color-bg-elevated)] data-[selected=true]:text-[var(--color-text-primary)]"
            >
              <LayoutDashboard className="h-4 w-4" />
              Dashboard
            </Command.Item>
            <Command.Item
              value="settings"
              onSelect={() => navigate("/settings")}
              className="flex items-center gap-2.5 rounded-md px-3 py-2 text-sm text-[var(--color-text-secondary)] cursor-pointer hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] data-[selected=true]:bg-[var(--color-bg-elevated)] data-[selected=true]:text-[var(--color-text-primary)]"
            >
              <Settings className="h-4 w-4" />
              Settings
            </Command.Item>
          </Command.Group>

          {/* Workspace */}
          {isWorkspace && (
            <Command.Group heading="Workspace" className="px-2 pb-1">
              <Command.Item
                value="workspace-overview"
                onSelect={() => navigate("/workspace")}
                className="flex items-center gap-2.5 rounded-md px-3 py-2 text-sm text-[var(--color-text-secondary)] cursor-pointer hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] data-[selected=true]:bg-[var(--color-bg-elevated)] data-[selected=true]:text-[var(--color-text-primary)]"
              >
                <Layers className="h-4 w-4" />
                Workspace Overview
              </Command.Item>
              <Command.Item
                value="workspace-contracts"
                onSelect={() => navigate("/workspace/contracts")}
                className="flex items-center gap-2.5 rounded-md px-3 py-2 text-sm text-[var(--color-text-secondary)] cursor-pointer hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] data-[selected=true]:bg-[var(--color-bg-elevated)] data-[selected=true]:text-[var(--color-text-primary)]"
              >
                <Link2 className="h-4 w-4" />
                Contracts
              </Command.Item>
              <Command.Item
                value="workspace-co-changes"
                onSelect={() => navigate("/workspace/co-changes")}
                className="flex items-center gap-2.5 rounded-md px-3 py-2 text-sm text-[var(--color-text-secondary)] cursor-pointer hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] data-[selected=true]:bg-[var(--color-bg-elevated)] data-[selected=true]:text-[var(--color-text-primary)]"
              >
                <GitMerge className="h-4 w-4" />
                Co-Changes
              </Command.Item>
            </Command.Group>
          )}

          {/* Repos */}
          {repos.length > 0 && (
            <Command.Group heading="Repositories" className="px-2 pb-1">
              {repos.map((repo) => (
                <Command.Item
                  key={repo.id}
                  value={`repo-${repo.name}`}
                  onSelect={() => navigate(`/repos/${repo.id}/overview`)}
                  className="flex items-center gap-2.5 rounded-md px-3 py-2 text-sm text-[var(--color-text-secondary)] cursor-pointer hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] data-[selected=true]:bg-[var(--color-bg-elevated)] data-[selected=true]:text-[var(--color-text-primary)]"
                >
                  <BookOpen className="h-4 w-4" />
                  {repo.name}
                </Command.Item>
              ))}
            </Command.Group>
          )}

          {/* File jump */}
          {activeRepo && fileMatches.length > 0 && (
            <Command.Group heading="Files" className="px-2 pb-1">
              {fileMatches.map((path) => {
                const name = path.split("/").pop() ?? path;
                const dir = path.slice(0, path.length - name.length);
                return (
                  <Command.Item
                    // Embed the query so cmdk's own filter keeps our pre-ranked
                    // matches visible instead of re-filtering them out.
                    key={path}
                    value={`file ${path} ${query}`}
                    onSelect={() =>
                      navigate(
                        `/repos/${activeRepo.id}/files/${path
                          .split("/")
                          .map(encodeURIComponent)
                          .join("/")}`,
                      )
                    }
                    className="flex items-center gap-2.5 rounded-md px-3 py-2 text-sm cursor-pointer hover:bg-[var(--color-bg-elevated)] data-[selected=true]:bg-[var(--color-bg-elevated)]"
                  >
                    <FileCode className="h-4 w-4 shrink-0 text-[var(--color-text-tertiary)]" />
                    <span className="min-w-0 truncate font-mono text-[13px]">
                      <span className="text-[var(--color-text-tertiary)]">
                        {truncatePath(dir, 36)}
                      </span>
                      <span className="text-[var(--color-text-primary)]">{name}</span>
                    </span>
                  </Command.Item>
                );
              })}
            </Command.Group>
          )}

          {/* Search results */}
          {results.length > 0 && (
            <Command.Group heading="Pages" className="px-2 pb-1">
              {results.map((r) => (
                <Command.Item
                  key={r.page_id}
                  value={`page-${r.title}`}
                  onSelect={() => {
                    // Prefer the active repo for context; fall back to the
                    // first one. File pages open their canonical entity page,
                    // everything else opens inside the docs SPA.
                    const repoId = activeRepo?.id ?? repos[0]?.id ?? "";
                    navigate(pageHref(repoId, r.page_id));
                  }}
                  className="flex flex-col items-start rounded-md px-3 py-2 text-sm cursor-pointer hover:bg-[var(--color-bg-elevated)] data-[selected=true]:bg-[var(--color-bg-elevated)]"
                >
                  <span className="text-[var(--color-text-primary)] font-medium">{r.title}</span>
                  <span className="text-xs text-[var(--color-text-tertiary)] font-mono">
                    {truncatePath(r.target_path, 50)}
                  </span>
                </Command.Item>
              ))}
            </Command.Group>
          )}
        </Command.List>

        <div className="border-t border-[var(--color-border-default)] px-4 py-2 flex items-center gap-4 text-xs text-[var(--color-text-tertiary)]">
          <span>↑↓ navigate</span>
          <span>↵ select</span>
          <span>esc close</span>
        </div>
      </div>
    </Command.Dialog>
  );
}
