"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  BookOpen,
  GitBranch,
  Lightbulb,
  MessageSquare,
  Search,
  Code2,
  BarChart3,
  Users,
  Flame,
  Trash2,
  Settings,
  ChevronDown,
  ChevronRight,
  Circle,
  PanelLeft,
} from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { AddRepoDialog } from "@/components/repos/add-repo-dialog";
import type { RepoResponse } from "@/lib/api/types";

interface NavItem {
  label: string;
  href: string;
  icon: React.ComponentType<{ className?: string }>;
}

const GLOBAL_NAV: NavItem[] = [
  { label: "Dashboard", href: "/", icon: LayoutDashboard },
  { label: "Settings", href: "/settings", icon: Settings },
];

function repoNavItems(repoId: string): NavItem[] {
  return [
    { label: "Chat", href: `/repos/${repoId}`, icon: MessageSquare },
    { label: "Search", href: `/repos/${repoId}/search`, icon: Search },
    { label: "Graph", href: `/repos/${repoId}/graph`, icon: GitBranch },
    { label: "Symbols", href: `/repos/${repoId}/symbols`, icon: Code2 },
    { label: "Coverage", href: `/repos/${repoId}/coverage`, icon: BarChart3 },
    { label: "Ownership", href: `/repos/${repoId}/ownership`, icon: Users },
    { label: "Hotspots", href: `/repos/${repoId}/hotspots`, icon: Flame },
    { label: "Dead Code", href: `/repos/${repoId}/dead-code`, icon: Trash2 },
    { label: "Decisions", href: `/repos/${repoId}/decisions`, icon: Lightbulb },
    { label: "Settings", href: `/repos/${repoId}/settings`, icon: Settings },
  ];
}

interface SidebarProps {
  repos?: RepoResponse[];
  activeRepoId?: string;
}

export function Sidebar({ repos = [], activeRepoId }: SidebarProps) {
  const pathname = usePathname();
  const [expandedRepos, setExpandedRepos] = React.useState<Set<string>>(
    activeRepoId ? new Set([activeRepoId]) : new Set(),
  );
  // On lg screens, start expanded if a repo is active
  const [collapsed, setCollapsed] = React.useState(false);

  const toggleRepo = (id: string) => {
    setExpandedRepos((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const isIconOnly = collapsed;

  return (
    <aside
      className={cn(
        "hidden md:flex h-full flex-col border-r border-[var(--color-border-default)] bg-[var(--color-bg-surface)] transition-all duration-200 shrink-0",
        isIconOnly ? "w-[52px]" : "w-[260px]",
      )}
    >
      {/* Logo */}
      <div className="flex h-12 items-center gap-2.5 px-3 border-b border-[var(--color-border-default)]">
        <div className="flex h-6 w-6 items-center justify-center rounded bg-[var(--color-accent-primary)] shrink-0">
          <BookOpen className="h-3.5 w-3.5 text-[var(--color-text-inverse)]" />
        </div>
        {!isIconOnly && (
          <span className="text-sm font-semibold text-[var(--color-text-primary)] tracking-tight flex-1 truncate">
            WikiCode
          </span>
        )}
        <button
          onClick={() => setCollapsed((c) => !c)}
          className="ml-auto shrink-0 rounded p-1 text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-secondary)] transition-colors"
          aria-label={isIconOnly ? "Expand sidebar" : "Collapse sidebar"}
        >
          <PanelLeft className={cn("h-3.5 w-3.5 transition-transform", isIconOnly && "rotate-180")} />
        </button>
      </div>

      <ScrollArea className="flex-1">
        <div className={cn("p-2", isIconOnly && "px-1.5")}>
          {/* Global nav */}
          <nav className="space-y-0.5">
            {GLOBAL_NAV.map((item) => (
              <SidebarNavItem
                key={item.href}
                item={item}
                isActive={pathname === item.href}
                iconOnly={isIconOnly}
              />
            ))}
          </nav>

          {repos.length > 0 && (
            <>
              {!isIconOnly && (
                <>
                  <Separator className="my-3" />
                  <p className="mb-1.5 px-2 text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
                    Repositories
                  </p>
                </>
              )}
              {isIconOnly && <Separator className="my-3" />}
              <div className="space-y-0.5">
                {repos.map((repo) => {
                  const isExpanded = expandedRepos.has(repo.id);
                  const isActive = activeRepoId === repo.id;
                  const navItems = repoNavItems(repo.id);

                  if (isIconOnly) {
                    return (
                      <Tooltip key={repo.id}>
                        <TooltipTrigger asChild>
                          <button
                            onClick={() => toggleRepo(repo.id)}
                            className={cn(
                              "flex w-full items-center justify-center rounded-md p-2 transition-colors hover:bg-[var(--color-bg-elevated)]",
                              isActive ? "text-[var(--color-accent-primary)]" : "text-[var(--color-text-tertiary)]",
                            )}
                            aria-label={repo.name}
                          >
                            <Circle className={cn("h-2.5 w-2.5", isActive ? "fill-[var(--color-accent-primary)]" : "fill-current")} />
                          </button>
                        </TooltipTrigger>
                        <TooltipContent side="right">{repo.name}</TooltipContent>
                      </Tooltip>
                    );
                  }

                  return (
                    <div key={repo.id}>
                      <button
                        onClick={() => toggleRepo(repo.id)}
                        className={cn(
                          "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors hover:bg-[var(--color-bg-elevated)]",
                          isActive
                            ? "text-[var(--color-text-primary)]"
                            : "text-[var(--color-text-secondary)]",
                        )}
                      >
                        <Circle
                          className={cn("h-2 w-2 shrink-0", isActive ? "fill-[var(--color-accent-primary)] text-[var(--color-accent-primary)]" : "fill-[var(--color-text-tertiary)] text-[var(--color-text-tertiary)]")}
                        />
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
                          {navItems.map((item) => (
                            <SidebarNavItem
                              key={item.href}
                              item={item}
                              isActive={pathname === item.href || pathname.startsWith(`${item.href}/`)}
                              size="sm"
                              iconOnly={false}
                            />
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {!isIconOnly && (
                <div className="mt-1 px-0.5">
                  <AddRepoDialog variant="sidebar" />
                </div>
              )}
            </>
          )}

          {repos.length === 0 && !isIconOnly && (
            <>
              <Separator className="my-3" />
              <div className="px-0.5">
                <AddRepoDialog variant="sidebar" />
              </div>
            </>
          )}
        </div>
      </ScrollArea>

      {/* Footer */}
      {!isIconOnly && (
        <div className="border-t border-[var(--color-border-default)] p-3">
          <p className="text-xs text-[var(--color-text-tertiary)] text-center">
            WikiCode v0.1.0
          </p>
        </div>
      )}
    </aside>
  );
}

function SidebarNavItem({
  item,
  isActive,
  size = "default",
  iconOnly = false,
}: {
  item: NavItem;
  isActive: boolean;
  size?: "default" | "sm";
  iconOnly?: boolean;
}) {
  const Icon = item.icon;

  if (iconOnly) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <Link
            href={item.href}
            aria-label={item.label}
            className={cn(
              "flex items-center justify-center rounded-md p-2 transition-colors",
              isActive
                ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
                : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]",
            )}
          >
            <Icon className="h-4 w-4 shrink-0" />
          </Link>
        </TooltipTrigger>
        <TooltipContent side="right">{item.label}</TooltipContent>
      </Tooltip>
    );
  }

  return (
    <Link
      href={item.href}
      className={cn(
        "flex items-center gap-2 rounded-md px-2 transition-colors",
        size === "sm" ? "py-1 text-xs" : "py-1.5 text-sm",
        isActive
          ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
          : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]",
      )}
    >
      <Icon className={cn("shrink-0", size === "sm" ? "h-3.5 w-3.5" : "h-4 w-4")} />
      <span className="truncate">{item.label}</span>
    </Link>
  );
}
