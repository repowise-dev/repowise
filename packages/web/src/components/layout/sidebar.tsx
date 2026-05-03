"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import Image from "next/image";
import {
  LayoutDashboard,
  Activity,
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
  Radar,
  DollarSign,
  Settings,
  ChevronDown,
  ChevronRight,
  Circle,
  PanelLeft,
  Layers,
  Link2,
  GitMerge,
} from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { ScrollArea } from "@repowise-dev/ui/ui/scroll-area";
import { Separator } from "@repowise-dev/ui/ui/separator";
import { Tooltip, TooltipContent, TooltipTrigger } from "@repowise-dev/ui/ui/tooltip";
import { AddRepoDialog } from "@/components/repos/add-repo-dialog";
import type { RepoResponse, WorkspaceResponse } from "@/lib/api/types";

interface NavItem {
  label: string;
  href: string;
  icon: React.ComponentType<{ className?: string }>;
}

const GLOBAL_NAV: NavItem[] = [
  { label: "Dashboard", href: "/", icon: LayoutDashboard },
  { label: "Settings", href: "/settings", icon: Settings },
];

const WORKSPACE_NAV: NavItem[] = [
  { label: "Overview", href: "/workspace", icon: Layers },
  { label: "Contracts", href: "/workspace/contracts", icon: Link2 },
  { label: "Co-Changes", href: "/workspace/co-changes", icon: GitMerge },
];


function repoNavItems(repoId: string): NavItem[] {
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
    { label: "Costs", href: `/repos/${repoId}/costs`, icon: DollarSign },
    { label: "Settings", href: `/repos/${repoId}/settings`, icon: Settings },
  ];
}

interface SidebarProps {
  repos?: RepoResponse[];
  activeRepoId?: string;
  workspace?: WorkspaceResponse | null;
}

export function Sidebar({ repos = [], activeRepoId, workspace }: SidebarProps) {
  const isWorkspace = workspace?.is_workspace ?? false;
  const pathname = usePathname();
  const derivedActiveRepoId = React.useMemo(() => {
    if (activeRepoId) return activeRepoId;
    const m = pathname?.match(/^\/repos\/([^/]+)/);
    return m ? m[1] : undefined;
  }, [activeRepoId, pathname]);
  const [expandedRepos, setExpandedRepos] = React.useState<Set<string>>(
    derivedActiveRepoId ? new Set([derivedActiveRepoId]) : new Set(),
  );
  React.useEffect(() => {
    if (derivedActiveRepoId) {
      setExpandedRepos((prev) => {
        if (prev.has(derivedActiveRepoId)) return prev;
        const next = new Set(prev);
        next.add(derivedActiveRepoId);
        return next;
      });
    }
  }, [derivedActiveRepoId]);
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
        isIconOnly ? "w-[56px]" : "w-[260px]",
      )}
    >
      {/* Logo */}
      <div className="flex h-14 items-center gap-3 px-4">
        <Image
          src="/repowise-logo.png"
          alt="repowise"
          width={28}
          height={28}
          className="shrink-0 drop-shadow-[0_0_8px_rgba(245,149,32,0.3)]"
        />
        {!isIconOnly && (
          <span className="text-base font-semibold text-[var(--color-text-primary)] tracking-tight flex-1 truncate">
            repowise
          </span>
        )}
        <button
          onClick={() => setCollapsed((c) => !c)}
          className="ml-auto shrink-0 rounded-md p-2.5 text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-secondary)] transition-colors"
          aria-label={isIconOnly ? "Expand sidebar" : "Collapse sidebar"}
          aria-expanded={!isIconOnly}
          aria-controls="sidebar-nav"
        >
          <PanelLeft className={cn("h-4 w-4 transition-transform", isIconOnly && "rotate-180")} />
        </button>
      </div>

      <ScrollArea className="flex-1" id="sidebar-nav">
        <div className={cn("px-3 py-2", isIconOnly && "px-2")}>
          {/* Global nav */}
          <nav className="space-y-1">
            {GLOBAL_NAV.map((item) => (
              <SidebarNavItem
                key={item.href}
                item={item}
                isActive={pathname === item.href}
                iconOnly={isIconOnly}
              />
            ))}
          </nav>

          {/* Workspace nav â€” only shown in workspace mode */}
          {isWorkspace && (
            <>
              {!isIconOnly && (
                <>
                  <Separator className="my-4" />
                  <p className="mb-2 px-2 text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
                    Workspace
                  </p>
                </>
              )}
              {isIconOnly && <Separator className="my-4" />}
              <nav className="space-y-1">
                {WORKSPACE_NAV.map((item) => (
                  <SidebarNavItem
                    key={item.href}
                    item={item}
                    isActive={
                      item.href === "/workspace"
                        ? pathname === "/workspace"
                        : pathname.startsWith(`${item.href}`)
                    }
                    iconOnly={isIconOnly}
                  />
                ))}
              </nav>
            </>
          )}

          {repos.length > 0 && (
            <>
              {!isIconOnly && (
                <>
                  <Separator className="my-4" />
                  <p className="mb-2 px-2 text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
                    Repositories
                  </p>
                </>
              )}
              {isIconOnly && <Separator className="my-4" />}
              <div className="space-y-0.5">
                {repos.map((repo) => {
                  const isExpanded = expandedRepos.has(repo.id);
                  const isActive = derivedActiveRepoId === repo.id;
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
                        aria-expanded={isExpanded}
                        aria-controls={`sidebar-repo-${repo.id}`}
                        className={cn(
                          "flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-sm transition-colors hover:bg-[var(--color-bg-elevated)]",
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
                          <ChevronDown className="h-4 w-4 shrink-0 opacity-40" />
                        ) : (
                          <ChevronRight className="h-4 w-4 shrink-0 opacity-40" />
                        )}
                      </button>
                      {isExpanded && (
                        <div id={`sidebar-repo-${repo.id}`} className="ml-3.5 mt-0.5 space-y-0.5 border-l border-[var(--color-border-default)] pl-3">
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
                <div className="mt-2 px-0.5">
                  <AddRepoDialog variant="sidebar" />
                </div>
              )}
            </>
          )}

          {repos.length === 0 && !isIconOnly && (
            <>
              <Separator className="my-4" />
              <div className="px-0.5">
                <AddRepoDialog variant="sidebar" />
              </div>
            </>
          )}
        </div>
      </ScrollArea>

      {/* Footer */}
      {!isIconOnly && (
        <div className="border-t border-[var(--color-border-default)] px-4 py-3">
          <p className="text-xs text-[var(--color-text-tertiary)]">
            repowise v0.1.0
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
              "flex items-center justify-center rounded-lg p-2.5 transition-colors",
              isActive
                ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
                : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]",
            )}
          >
            <Icon className="h-[18px] w-[18px] shrink-0" />
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
        "flex items-center gap-2.5 rounded-lg px-2 transition-colors",
        size === "sm" ? "py-1.5 text-[13px]" : "py-2 text-sm",
        isActive
          ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
          : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]",
      )}
    >
      <Icon className={cn("shrink-0", size === "sm" ? "h-4 w-4" : "h-[18px] w-[18px]")} />
      <span className="truncate">{item.label}</span>
    </Link>
  );
}

