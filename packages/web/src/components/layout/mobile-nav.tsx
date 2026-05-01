"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import Image from "next/image";
import {
  Menu,
  Activity,
  BookOpen,
  LayoutDashboard,
  Lightbulb,
  MessageSquare,
  Settings,
  Search,
  GitBranch,
  Code2,
  BarChart3,
  Users,
  Flame,
  Trash2,
  Radar,
  ChevronDown,
  ChevronRight,
  Circle,
  SlidersHorizontal,
  Layers,
  Link2,
  GitMerge,
} from "lucide-react";
import { Button } from "@repowise/ui/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@repowise/ui/ui/sheet";
import { ScrollArea } from "@repowise/ui/ui/scroll-area";
import { Separator } from "@repowise/ui/ui/separator";
import { AddRepoDialog } from "@/components/repos/add-repo-dialog";
import { cn } from "@/lib/utils/cn";
import type { RepoResponse, WorkspaceResponse } from "@/lib/api/types";

const GLOBAL_NAV = [
  { label: "Dashboard", href: "/", icon: LayoutDashboard },
  { label: "Settings", href: "/settings", icon: Settings },
];

function repoNavItems(repoId: string) {
  return [
    { label: "Overview", href: `/repos/${repoId}/overview`, icon: Activity },
    { label: "Chat", href: `/repos/${repoId}`, icon: MessageSquare },
    { label: "Docs", href: `/repos/${repoId}/docs`, icon: BookOpen },
    { label: "Search", href: `/repos/${repoId}/search`, icon: Search },
    { label: "Graph", href: `/repos/${repoId}/graph`, icon: GitBranch },
    { label: "Symbols", href: `/repos/${repoId}/symbols`, icon: Code2 },
    { label: "Coverage", href: `/repos/${repoId}/coverage`, icon: BarChart3 },
    { label: "Ownership", href: `/repos/${repoId}/ownership`, icon: Users },
    { label: "Hotspots", href: `/repos/${repoId}/hotspots`, icon: Flame },
    { label: "Dead Code", href: `/repos/${repoId}/dead-code`, icon: Trash2 },
    { label: "Blast Radius", href: `/repos/${repoId}/blast-radius`, icon: Radar },
    { label: "Decisions", href: `/repos/${repoId}/decisions`, icon: Lightbulb },
    { label: "Settings", href: `/repos/${repoId}/settings`, icon: SlidersHorizontal },
  ];
}

const WORKSPACE_NAV = [
  { label: "Overview", href: "/workspace", icon: Layers },
  { label: "Contracts", href: "/workspace/contracts", icon: Link2 },
  { label: "Co-Changes", href: "/workspace/co-changes", icon: GitMerge },
];

interface MobileNavProps {
  repos?: RepoResponse[];
  workspace?: WorkspaceResponse | null;
}

export function MobileNav({ repos = [], workspace }: MobileNavProps) {
  const isWorkspace = workspace?.is_workspace ?? false;
  const [open, setOpen] = React.useState(false);
  const pathname = usePathname();
  const activeRepoId = React.useMemo(() => {
    const m = pathname?.match(/^\/repos\/([^/]+)/);
    return m ? m[1] : undefined;
  }, [pathname]);
  const [expandedRepos, setExpandedRepos] = React.useState<Set<string>>(
    activeRepoId ? new Set([activeRepoId]) : new Set(),
  );
  React.useEffect(() => {
    if (activeRepoId) {
      setExpandedRepos((prev) => {
        if (prev.has(activeRepoId)) return prev;
        const next = new Set(prev);
        next.add(activeRepoId);
        return next;
      });
    }
  }, [activeRepoId]);

  const toggleRepo = (id: string) => {
    setExpandedRepos((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // Close sheet on navigation
  React.useEffect(() => {
    setOpen(false);
  }, [pathname]);

  return (
    <div className="flex md:hidden min-h-14 items-center gap-3 px-4 border-b border-[var(--color-border-default)] bg-[var(--color-bg-surface)] shrink-0">
      <Button
        variant="ghost"
        size="icon"
        onClick={() => setOpen(true)}
        aria-label="Open navigation menu"
        className="h-11 w-11"
      >
        <Menu className="h-5 w-5" />
      </Button>
      <div className="flex items-center gap-2 min-w-0 flex-1">
        <Image
          src="/repowise-logo.png"
          alt="repowise"
          width={24}
          height={24}
          className="shrink-0 drop-shadow-[0_0_8px_rgba(245,149,32,0.3)]"
        />
        <span className="text-base font-semibold text-[var(--color-text-primary)] tracking-tight truncate">
          repowise
        </span>
      </div>
      <Button
        variant="ghost"
        size="icon"
        onClick={() => {
          window.dispatchEvent(new CustomEvent("repowise:open-command-palette"));
        }}
        aria-label="Open search"
        className="h-11 w-11"
      >
        <Search className="h-5 w-5" />
      </Button>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="left" className="w-72 p-0">
          <SheetHeader className="border-b border-[var(--color-border-default)] h-14 flex-row items-center gap-3 py-0 px-4">
            <Image
              src="/repowise-logo.png"
              alt="repowise"
              width={28}
              height={28}
              className="shrink-0 drop-shadow-[0_0_8px_rgba(245,149,32,0.3)]"
            />
            <SheetTitle className="text-base">repowise</SheetTitle>
          </SheetHeader>

          <ScrollArea className="flex-1">
            <div className="px-3 py-3">
              <nav className="space-y-1">
                {GLOBAL_NAV.map((item) => {
                  const Icon = item.icon;
                  const isActive = pathname === item.href;
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      className={cn(
                        "flex items-center gap-2.5 rounded-lg px-2 py-2 text-sm transition-colors",
                        isActive
                          ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
                          : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]",
                      )}
                    >
                      <Icon className="h-[18px] w-[18px] shrink-0" />
                      {item.label}
                    </Link>
                  );
                })}
              </nav>

              {isWorkspace && (
                <>
                  <Separator className="my-4" />
                  <p className="mb-2 px-2 text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
                    Workspace
                  </p>
                  <nav className="space-y-1">
                    {WORKSPACE_NAV.map((item) => {
                      const Icon = item.icon;
                      const isActive =
                        item.href === "/workspace"
                          ? pathname === "/workspace"
                          : pathname.startsWith(`${item.href}`);
                      return (
                        <Link
                          key={item.href}
                          href={item.href}
                          className={cn(
                            "flex items-center gap-2.5 rounded-lg px-2 py-2 text-sm transition-colors",
                            isActive
                              ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
                              : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]",
                          )}
                        >
                          <Icon className="h-[18px] w-[18px] shrink-0" />
                          {item.label}
                        </Link>
                      );
                    })}
                  </nav>
                </>
              )}

              {repos.length > 0 && (
                <>
                  <Separator className="my-4" />
                  <p className="mb-2 px-2 text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
                    Repositories
                  </p>
                  <div className="space-y-0.5">
                    {repos.map((repo) => {
                      const isExpanded = expandedRepos.has(repo.id);
                      const navItems = repoNavItems(repo.id);
                      return (
                        <div key={repo.id}>
                          <button
                            onClick={() => toggleRepo(repo.id)}
                            aria-expanded={isExpanded}
                            className="flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-sm transition-colors hover:bg-[var(--color-bg-elevated)] text-[var(--color-text-secondary)]"
                          >
                            <Circle className="h-2 w-2 shrink-0 fill-[var(--color-text-tertiary)] text-[var(--color-text-tertiary)]" />
                            <span className="flex-1 truncate text-left font-medium">
                              {repo.name}
                            </span>
                            {isExpanded ? (
                              <ChevronDown className="h-4 w-4 shrink-0 opacity-40" />
                            ) : (
                              <ChevronRight className="h-4 w-4 shrink-0 opacity-40" />
                            )}
                          </button>
                          {isExpanded && (
                            <div className="ml-3.5 mt-0.5 space-y-0.5 border-l border-[var(--color-border-default)] pl-3">
                              {navItems.map((item) => {
                                const Icon = item.icon;
                                const isActive =
                                  pathname === item.href ||
                                  pathname.startsWith(`${item.href}/`);
                                return (
                                  <Link
                                    key={item.href}
                                    href={item.href}
                                    className={cn(
                                      "flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-[13px] transition-colors",
                                      isActive
                                        ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
                                        : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]",
                                    )}
                                  >
                                    <Icon className="h-4 w-4 shrink-0" />
                                    {item.label}
                                  </Link>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                  <div className="mt-2 px-0.5">
                    <AddRepoDialog variant="sidebar" />
                  </div>
                </>
              )}

              {repos.length === 0 && (
                <>
                  <Separator className="my-4" />
                  <div className="px-0.5">
                    <AddRepoDialog variant="sidebar" />
                  </div>
                </>
              )}
            </div>
          </ScrollArea>

          <div className="border-t border-[var(--color-border-default)] px-4 py-3">
            <p className="text-xs text-[var(--color-text-tertiary)]">repowise v0.1.0</p>
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
