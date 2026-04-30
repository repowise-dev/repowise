"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Command } from "cmdk";
import { Search, LayoutDashboard, Settings, BookOpen, Layers, Link2, GitMerge } from "lucide-react";
import { useSearch } from "@/lib/hooks/use-search";
import { truncatePath } from "@/lib/utils/format";
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

  const { results, isLoading } = useSearch(query, { limit: 8 });

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
            placeholder="Search pages, navigate repos…"
            className="flex-1 bg-transparent text-sm text-[var(--color-text-primary)] outline-none placeholder:text-[var(--color-text-tertiary)]"
          />
          <kbd className="hidden sm:inline-flex items-center rounded border border-[var(--color-border-default)] px-1.5 py-0.5 text-xs text-[var(--color-text-tertiary)] font-mono">
            ESC
          </kbd>
        </div>

        <Command.List className="max-h-80 overflow-y-auto py-2">
          <Command.Empty className="px-4 py-8 text-center text-sm text-[var(--color-text-tertiary)]">
            {isLoading ? "Searching…" : "No results found."}
          </Command.Empty>

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
                  onSelect={() => navigate(`/repos/${repo.id}`)}
                  className="flex items-center gap-2.5 rounded-md px-3 py-2 text-sm text-[var(--color-text-secondary)] cursor-pointer hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] data-[selected=true]:bg-[var(--color-bg-elevated)] data-[selected=true]:text-[var(--color-text-primary)]"
                >
                  <BookOpen className="h-4 w-4" />
                  {repo.name}
                </Command.Item>
              ))}
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
                    // Use first available repo as context for the wiki route.
                    // The repo ID is only used for supplemental data (git panel, breadcrumb).
                    const fallbackRepoId = repos[0]?.id ?? "";
                    navigate(`/repos/${fallbackRepoId}/wiki/${encodeURIComponent(r.page_id)}`);
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
