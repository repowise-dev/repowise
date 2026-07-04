"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { BrandLogo } from "./brand-logo";
import {
  ChevronDown,
  ChevronRight,
  Circle,
  PanelLeft,
  Search,
} from "lucide-react";
import { cn } from "@/lib/utils/cn";
import {
  GLOBAL_NAV,
  WORKSPACE_NAV,
  repoNavGroups,
  isNavItemActive,
  type NavItem,
} from "./nav-items";
import { ScrollArea } from "@repowise-dev/ui/ui/scroll-area";
import { Separator } from "@repowise-dev/ui/ui/separator";
import { Tooltip, TooltipContent, TooltipTrigger } from "@repowise-dev/ui/ui/tooltip";
import { ThemeToggle } from "@repowise-dev/ui/shared/theme-toggle";
import { AddRepoDialog } from "@/components/repos/add-repo-dialog";
import { VersionFooter } from "./version-footer";
import type { RepoResponse, WorkspaceResponse } from "@/lib/api/types";

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
  // Docs is a reading surface — auto-collapse the sidebar on entering it so
  // the page gets the width, and restore the previous state on leaving.
  // Manual toggles always win while the route type is unchanged.
  const isDocsRoute = /^\/repos\/[^/]+\/docs(\/|$)/.test(pathname ?? "");
  const [collapsed, setCollapsed] = React.useState(isDocsRoute);
  const preDocsCollapsed = React.useRef(false);
  const wasDocsRoute = React.useRef(isDocsRoute);
  React.useEffect(() => {
    if (isDocsRoute === wasDocsRoute.current) return;
    wasDocsRoute.current = isDocsRoute;
    if (isDocsRoute) {
      setCollapsed((c) => {
        preDocsCollapsed.current = c;
        return true;
      });
    } else {
      setCollapsed(preDocsCollapsed.current);
    }
  }, [isDocsRoute]);

  // Workspace group: collapsed unless the user is on a workspace route, so
  // the (more used) per-repo navigation leads. Tracks route changes in both
  // directions; manual toggles win while the route type is unchanged.
  const isWorkspaceRoute = pathname?.startsWith("/workspace") ?? false;
  const [workspaceNavOpen, setWorkspaceNavOpen] = React.useState(isWorkspaceRoute);
  const wasWorkspaceRoute = React.useRef(isWorkspaceRoute);
  React.useEffect(() => {
    if (isWorkspaceRoute === wasWorkspaceRoute.current) return;
    wasWorkspaceRoute.current = isWorkspaceRoute;
    setWorkspaceNavOpen(isWorkspaceRoute);
  }, [isWorkspaceRoute]);

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
      {/* Logo. Collapsed (56px) can't fit logo + toggle on one row — the
          button used to spill out over the breadcrumb — so stack them. */}
      <div
        className={cn(
          "flex items-center gap-3",
          isIconOnly ? "flex-col gap-1.5 px-0 pt-3 pb-1" : "h-14 px-4",
        )}
      >
        <BrandLogo size={28} />
        {!isIconOnly && (
          <span className="text-base font-semibold text-[var(--color-text-primary)] tracking-tight flex-1 truncate">
            repowise
          </span>
        )}
        <button
          onClick={() => setCollapsed((c) => !c)}
          className={cn(
            "shrink-0 rounded-md p-2.5 text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-secondary)] transition-colors",
            !isIconOnly && "ml-auto",
          )}
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
            <SidebarSearchButton iconOnly={isIconOnly} />
          </nav>

          {/* Workspace nav — only shown in workspace mode. Collapsed by
              default: per-repo navigation is the primary surface, so the
              cross-repo views tuck behind a toggle unless one is active. */}
          {isWorkspace && (
            <>
              <Separator className="my-4" />
              {!isIconOnly && (
                <button
                  onClick={() => setWorkspaceNavOpen((v) => !v)}
                  aria-expanded={workspaceNavOpen}
                  aria-controls="sidebar-workspace-nav"
                  className="mb-1 flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-secondary)]"
                >
                  <span className="flex-1 truncate text-left">Workspace</span>
                  {workspaceNavOpen ? (
                    <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-60" />
                  ) : (
                    <ChevronRight className="h-3.5 w-3.5 shrink-0 opacity-60" />
                  )}
                </button>
              )}
              {(isIconOnly || workspaceNavOpen) && (
                <nav className="space-y-1" id="sidebar-workspace-nav">
                  {(isIconOnly && !isWorkspaceRoute
                    ? WORKSPACE_NAV.slice(0, 1)
                    : WORKSPACE_NAV
                  ).map((item) => (
                    <SidebarNavItem
                      key={item.href}
                      item={item}
                      isActive={item.exact ? pathname === item.href : pathname.startsWith(`${item.href}`)}
                      iconOnly={isIconOnly}
                    />
                  ))}
                </nav>
              )}
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
                  const navGroups = repoNavGroups(repo.id);
                  const needsIndex =
                    repo.workspace_status === "needs_index" ||
                    repo.id.startsWith("ws:");
                  const isMissing = repo.workspace_status === "missing_dir";

                  if (needsIndex || isMissing) {
                    // Synthetic / unindexed workspace entry — show as a
                    // disabled row with a status hint. The Index/Sync
                    // CTA lives in the Workspace dashboard.
                    if (isIconOnly) {
                      return (
                        <Tooltip key={repo.id}>
                          <TooltipTrigger asChild>
                            <div
                              className="flex w-full items-center justify-center rounded-md p-2 text-[var(--color-text-tertiary)] opacity-60"
                              aria-label={`${repo.name} (${isMissing ? "missing" : "needs index"})`}
                            >
                              <Circle className="h-2.5 w-2.5 stroke-current" />
                            </div>
                          </TooltipTrigger>
                          <TooltipContent side="right">
                            {repo.workspace_alias ?? repo.name}
                            {" — "}
                            {isMissing ? "directory missing" : "needs indexing"}
                          </TooltipContent>
                        </Tooltip>
                      );
                    }
                    return (
                      <Link
                        key={repo.id}
                        href="/workspace"
                        className="flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-sm text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-elevated)]"
                        title={
                          isMissing
                            ? "Directory missing — open Workspace to remove or fix"
                            : "Not indexed yet — open Workspace to index"
                        }
                      >
                        <Circle className="h-2 w-2 shrink-0 stroke-current" />
                        <span className="flex-1 truncate text-left font-medium">
                          {repo.workspace_alias ?? repo.name}
                        </span>
                        <span className="shrink-0 rounded-full bg-[var(--color-bg-elevated)] px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-[var(--color-text-tertiary)]">
                          {isMissing ? "missing" : "index"}
                        </span>
                      </Link>
                    );
                  }

                  if (isIconOnly) {
                    // Collapsed: the active repo still exposes its full nav as
                    // icons (so the repository-view options stay reachable, e.g.
                    // on Docs where the sidebar auto-collapses); inactive repos
                    // stay a single dot that jumps to their overview.
                    if (isActive) {
                      return (
                        <div key={repo.id} className="space-y-0.5">
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <div
                                className="flex w-full items-center justify-center rounded-md p-2 text-[var(--color-accent-primary)]"
                                aria-label={repo.name}
                              >
                                <Circle className="h-2.5 w-2.5 fill-[var(--color-accent-primary)]" />
                              </div>
                            </TooltipTrigger>
                            <TooltipContent side="right">{repo.name}</TooltipContent>
                          </Tooltip>
                          {navGroups.map((group, gi) => (
                            <React.Fragment key={group.label ?? gi}>
                              {gi > 0 && <Separator className="my-1.5" />}
                              {group.items.map((item) => (
                                <SidebarNavItem
                                  key={item.href}
                                  item={item}
                                  isActive={isNavItemActive(item, pathname)}
                                  iconOnly
                                />
                              ))}
                            </React.Fragment>
                          ))}
                        </div>
                      );
                    }
                    return (
                      <Tooltip key={repo.id}>
                        <TooltipTrigger asChild>
                          <Link
                            href={`/repos/${repo.id}/overview`}
                            className="flex w-full items-center justify-center rounded-md p-2 text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-secondary)]"
                            aria-label={repo.name}
                          >
                            <Circle className="h-2.5 w-2.5 fill-current" />
                          </Link>
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
                          {navGroups.map((group, gi) => (
                            <React.Fragment key={group.label ?? gi}>
                              {group.label ? (
                                <p className="px-2 pt-2 pb-0.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
                                  {group.label}
                                </p>
                              ) : gi > 0 ? (
                                <div className="pt-1.5" />
                              ) : null}
                              {group.items.map((item) => (
                                <SidebarNavItem
                                  key={item.href}
                                  item={item}
                                  isActive={isNavItemActive(item, pathname)}
                                  size="sm"
                                  iconOnly={false}
                                />
                              ))}
                            </React.Fragment>
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
              {/* First run: no repos to list, so the add action carries the sidebar. */}
              <div className="px-0.5">
                <AddRepoDialog />
              </div>
            </>
          )}
        </div>
      </ScrollArea>

      {/* Footer */}
      {!isIconOnly && (
        <div className="flex flex-col gap-3 border-t border-[var(--color-border-default)] px-4 py-3">
          <ThemeToggle className="w-full justify-between" />
          <VersionFooter />
        </div>
      )}
    </aside>
  );
}

function SidebarSearchButton({ iconOnly }: { iconOnly: boolean }) {
  const openPalette = () =>
    window.dispatchEvent(new CustomEvent("repowise:open-command-palette"));

  if (iconOnly) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            onClick={openPalette}
            aria-label="Search"
            className="flex w-full items-center justify-center rounded-lg p-2.5 text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
          >
            <Search className="h-[18px] w-[18px] shrink-0" />
          </button>
        </TooltipTrigger>
        <TooltipContent side="right">Search</TooltipContent>
      </Tooltip>
    );
  }

  return (
    <button
      onClick={openPalette}
      className="flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-sm text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
    >
      <Search className="h-[18px] w-[18px] shrink-0" />
      <span className="flex-1 truncate text-left">Search</span>
      <kbd className="rounded border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-1.5 py-0.5 text-[10px] text-[var(--color-text-tertiary)]">
        ⌘K
      </kbd>
    </button>
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

