"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import { BrandLogo } from "./brand-logo";
import {
  Menu,
  Search,
  ChevronDown,
  ChevronRight,
  Circle,
} from "lucide-react";
import { Button } from "@repowise-dev/ui/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@repowise-dev/ui/ui/sheet";
import { ScrollArea } from "@repowise-dev/ui/ui/scroll-area";
import { Separator } from "@repowise-dev/ui/ui/separator";
import { AddRepoDialog } from "@/components/repos/add-repo-dialog";
import { VersionFooter } from "./version-footer";
import { NewMark } from "./new-mark";
import { useIsNewSurface } from "@/lib/hooks/use-new-surfaces";
import { FeedbackButton } from "./feedback-button";
import { LanguageSwitcher } from "./language-switcher";
import { FooterSetupPill } from "@/components/setup/setup-pill";
import { ThemeToggle } from "@repowise-dev/ui/shared/theme-toggle";
import { cn } from "@/lib/utils/cn";
import { GLOBAL_NAV, WORKSPACE_NAV, isNavItemActive, repoNavGroups } from "./nav-items";
import { NAV_ROW_ACTIVE, NAV_ROW_IDLE, NavGroupList } from "./nav-group-list";
import type { RepoResponse, WorkspaceResponse } from "@/lib/api/types";

interface MobileNavProps {
  repos?: RepoResponse[];
  workspace?: WorkspaceResponse | null;
}

export function MobileNav({ repos = [], workspace }: MobileNavProps) {
  const t = useTranslations("nav");
  const ts = useTranslations("shell");
  const isWorkspace = workspace?.is_workspace ?? false;
  const isNew = useIsNewSurface();
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
    <div className="flex md:hidden print:hidden min-h-14 items-center gap-3 px-4 border-b border-[var(--color-border-default)] bg-[var(--color-bg-surface)] shrink-0">
      <Button
        variant="ghost"
        size="icon"
        onClick={() => setOpen(true)}
        aria-label={ts("openNavMenu")}
        className="h-11 w-11"
      >
        <Menu className="h-5 w-5" />
      </Button>
      <div className="flex items-center gap-2 min-w-0 flex-1">
        <BrandLogo size={24} />
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
        aria-label={ts("openSearch")}
        className="h-11 w-11"
      >
        <Search className="h-5 w-5" />
      </Button>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="left" className="w-72 p-0">
          <SheetHeader className="border-b border-[var(--color-border-default)] h-14 flex-row items-center gap-3 py-0 px-4">
            <BrandLogo size={28} />
            <SheetTitle className="text-base">repowise</SheetTitle>
          </SheetHeader>

          <ScrollArea className="flex-1">
            <div className="px-3 py-3">
              <nav className="space-y-1">
                {GLOBAL_NAV.map((item) => {
                  const Icon = item.icon;
                  const isActive = isNavItemActive(item, pathname);
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      className={cn(
                        "flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-base transition-colors",
                        isActive ? NAV_ROW_ACTIVE : NAV_ROW_IDLE,
                      )}
                    >
                      <span className="flex h-4 w-4 shrink-0 items-center justify-center">
                        <Icon className="h-4 w-4" />
                      </span>
                      <span className="truncate">{t(item.labelKey)}</span>
                      {isNew(item.href) && <NewMark />}
                    </Link>
                  );
                })}
              </nav>

              {isWorkspace && (
                <>
                  <p className="mb-1 mt-4 px-2 text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
                    {ts("workspace")}
                  </p>
                  {/* Children of the Workspace group, same as on desktop:
                      indented, ruled, and at the nested size. */}
                  <nav className="ml-3.5 space-y-0.5 border-l border-[var(--color-border-default)] pl-3">
                    {WORKSPACE_NAV.map((item) => {
                      const Icon = item.icon;
                      const isActive = (item as { exact?: boolean }).exact
                        ? pathname === item.href
                        : pathname.startsWith(`${item.href}`);
                      return (
                        <Link
                          key={item.href}
                          href={item.href}
                          className={cn(
                            "flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-xs transition-colors",
                            isActive ? NAV_ROW_ACTIVE : NAV_ROW_IDLE,
                          )}
                        >
                          <span className="flex h-4 w-4 shrink-0 items-center justify-center">
                            <Icon className="h-4 w-4" />
                          </span>
                          <span className="truncate">{t(item.labelKey)}</span>
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
                    {ts("repositories")}
                  </p>
                  <div className="space-y-0.5">
                    {repos.map((repo) => {
                      const isExpanded = expandedRepos.has(repo.id);
                      const navGroups = repoNavGroups(repo.id);
                      return (
                        <div key={repo.id}>
                          <button
                            onClick={() => toggleRepo(repo.id)}
                            aria-expanded={isExpanded}
                            title={repo.name}
                            className="flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-base transition-colors hover:bg-[var(--color-bg-elevated)] text-[var(--color-text-secondary)]"
                          >
                            <span className="flex h-4 w-4 shrink-0 items-center justify-center">
                              <Circle className="h-2 w-2 fill-[var(--color-text-tertiary)] text-[var(--color-text-tertiary)]" />
                            </span>
                            <span className="min-w-0 flex-1 truncate text-left">
                              {repo.name}
                            </span>
                            {isExpanded ? (
                              <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-40" />
                            ) : (
                              <ChevronRight className="h-3.5 w-3.5 shrink-0 opacity-40" />
                            )}
                          </button>
                          {isExpanded && (
                            <div className="ml-3.5 mt-0.5 space-y-0.5 border-l border-[var(--color-border-default)] pl-3">
                              <NavGroupList
                                groups={navGroups}
                                pathname={pathname}
                                idPrefix={`mobile-repo-${repo.id}`}
                                renderItem={(item, isActive) => {
                                    const Icon = item.icon;
                                    return (
                                      <Link
                                        href={item.href}
                                        className={cn(
                                          "flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-xs transition-colors",
                                          isActive ? NAV_ROW_ACTIVE : NAV_ROW_IDLE,
                                        )}
                                      >
                                        <span className="flex h-4 w-4 shrink-0 items-center justify-center">
                                          <Icon className="h-4 w-4" />
                                        </span>
                                        <span className="truncate">{t(item.labelKey)}</span>
                                        {isNew(item.href) && <NewMark />}
                                      </Link>
                                    );
                                }}
                              />
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

          <div className="flex flex-col gap-2 border-t border-[var(--color-border-default)] px-4 py-2.5">
            <FooterSetupPill repos={repos} activeRepoId={activeRepoId} />
            <FeedbackButton />
            {/* Mobile had no theme control anywhere in the shell, so the
                preference was simply unreachable on a phone. It shares the
                version row, same as the desktop footer. */}
            <div className="flex items-center justify-between gap-2">
              <VersionFooter />
              <div className="flex items-center gap-1">
                <LanguageSwitcher />
                <ThemeToggle
                  switchToDarkLabel={ts("switchToDarkTheme")}
                  switchToLightLabel={ts("switchToLightTheme")}
                />
              </div>
            </div>
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
