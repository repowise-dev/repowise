"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Menu,
  BookOpen,
  LayoutDashboard,
  Settings,
  Search,
  GitBranch,
  Code2,
  BarChart3,
  Users,
  Flame,
  Trash2,
  ChevronDown,
  ChevronRight,
  Circle,
  SlidersHorizontal,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { AddRepoDialog } from "@/components/repos/add-repo-dialog";
import { cn } from "@/lib/utils/cn";
import type { RepoResponse } from "@/lib/api/types";

const GLOBAL_NAV = [
  { label: "Dashboard", href: "/", icon: LayoutDashboard },
  { label: "Settings", href: "/settings", icon: Settings },
];

function repoNavItems(repoId: string) {
  return [
    { label: "Overview", href: `/repos/${repoId}`, icon: BookOpen },
    { label: "Search", href: `/repos/${repoId}/search`, icon: Search },
    { label: "Graph", href: `/repos/${repoId}/graph`, icon: GitBranch },
    { label: "Symbols", href: `/repos/${repoId}/symbols`, icon: Code2 },
    { label: "Coverage", href: `/repos/${repoId}/coverage`, icon: BarChart3 },
    { label: "Ownership", href: `/repos/${repoId}/ownership`, icon: Users },
    { label: "Hotspots", href: `/repos/${repoId}/hotspots`, icon: Flame },
    { label: "Dead Code", href: `/repos/${repoId}/dead-code`, icon: Trash2 },
    { label: "Settings", href: `/repos/${repoId}/settings`, icon: SlidersHorizontal },
  ];
}

interface MobileNavProps {
  repos?: RepoResponse[];
}

export function MobileNav({ repos = [] }: MobileNavProps) {
  const [open, setOpen] = React.useState(false);
  const pathname = usePathname();
  const [expandedRepos, setExpandedRepos] = React.useState<Set<string>>(new Set());

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
    <div className="flex md:hidden h-12 items-center gap-3 px-4 border-b border-[var(--color-border-default)] bg-[var(--color-bg-surface)] shrink-0">
      <Button
        variant="ghost"
        size="icon"
        onClick={() => setOpen(true)}
        aria-label="Open navigation menu"
        className="h-8 w-8"
      >
        <Menu className="h-5 w-5" />
      </Button>
      {/* Current page breadcrumb */}
      <div className="flex items-center gap-1.5 min-w-0">
        <div className="flex h-5 w-5 items-center justify-center rounded bg-[var(--color-accent-primary)] shrink-0">
          <BookOpen className="h-3 w-3 text-[var(--color-text-inverse)]" />
        </div>
        <span className="text-sm font-semibold text-[var(--color-text-primary)] tracking-tight truncate">
          repowise
        </span>
      </div>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="left" className="p-0">
          <SheetHeader className="border-b border-[var(--color-border-default)] h-12 flex-row items-center gap-2.5 py-0">
            <div className="flex h-6 w-6 items-center justify-center rounded bg-[var(--color-accent-primary)] shrink-0">
              <BookOpen className="h-3.5 w-3.5 text-[var(--color-text-inverse)]" />
            </div>
            <SheetTitle>repowise</SheetTitle>
          </SheetHeader>

          <ScrollArea className="flex-1">
            <div className="p-2">
              <nav className="space-y-0.5">
                {GLOBAL_NAV.map((item) => {
                  const Icon = item.icon;
                  const isActive = pathname === item.href;
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      className={cn(
                        "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
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
              </nav>

              {repos.length > 0 && (
                <>
                  <Separator className="my-3" />
                  <p className="mb-1.5 px-2 text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
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
                            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors hover:bg-[var(--color-bg-elevated)] text-[var(--color-text-secondary)]"
                          >
                            <Circle className="h-2 w-2 shrink-0 fill-[var(--color-text-tertiary)] text-[var(--color-text-tertiary)]" />
                            <span className="flex-1 truncate text-left font-medium">
                              {repo.name}
                            </span>
                            {isExpanded ? (
                              <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-50" />
                            ) : (
                              <ChevronRight className="h-3.5 w-3.5 shrink-0 opacity-50" />
                            )}
                          </button>
                          {isExpanded && (
                            <div className="ml-3 mt-0.5 space-y-0.5 border-l border-[var(--color-border-default)] pl-3">
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
                                      "flex items-center gap-2 rounded-md px-2 py-1 text-xs transition-colors",
                                      isActive
                                        ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
                                        : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]",
                                    )}
                                  >
                                    <Icon className="h-3.5 w-3.5 shrink-0" />
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
                  <div className="mt-1 px-0.5">
                    <AddRepoDialog variant="sidebar" />
                  </div>
                </>
              )}

              {repos.length === 0 && (
                <>
                  <Separator className="my-3" />
                  <div className="px-0.5">
                    <AddRepoDialog variant="sidebar" />
                  </div>
                </>
              )}
            </div>
          </ScrollArea>

          <div className="border-t border-[var(--color-border-default)] p-3">
            <p className="text-xs text-[var(--color-text-tertiary)] text-center">repowise v0.1.0</p>
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
