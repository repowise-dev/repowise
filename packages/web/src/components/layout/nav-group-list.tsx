"use client";

import * as React from "react";
import { useTranslations } from "next-intl";
import { ChevronDown, ChevronRight } from "lucide-react";
import { isNavItemActive, type NavGroup, type NavItem } from "./nav-items";

const STORAGE_KEY = "repowise:nav:collapsed-groups";

const GROUP_LABEL =
  "flex w-full items-center gap-1 px-2 pt-2 pb-0.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]";

function readCollapsed(): Set<string> {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(parsed) ? parsed.filter((v) => typeof v === "string") : []);
  } catch {
    return new Set();
  }
}

/**
 * Which labelled nav groups the reader folded away. A per-browser convenience,
 * so it lives in localStorage; it starts empty (every group open) and is read
 * after mount so the server and first client render agree.
 */
function useCollapsedNavGroups(): [Set<string>, (id: string) => void] {
  const [collapsed, setCollapsed] = React.useState<Set<string>>(() => new Set());
  React.useEffect(() => setCollapsed(readCollapsed()), []);
  const toggle = React.useCallback((id: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      try {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify([...next]));
      } catch {
        // Storage blocked: the fold still works for this page view.
      }
      return next;
    });
  }, []);
  return [collapsed, toggle];
}

/**
 * A repository's nav groups with collapsible labelled sections, shared by the
 * desktop sidebar and the mobile sheet so the two cannot drift. The host
 * renders each item, since the two surfaces size their rows differently.
 *
 * A group holding the current page never folds shut: hiding where you are is
 * a lost reader, not a tidier sidebar.
 */
export function NavGroupList({
  groups,
  pathname,
  idPrefix,
  renderItem,
}: {
  groups: NavGroup[];
  pathname: string;
  /** Prefix for the groups' element ids, unique per rendered list. */
  idPrefix: string;
  renderItem: (item: NavItem, isActive: boolean) => React.ReactNode;
}) {
  const t = useTranslations("nav");
  const [collapsed, toggle] = useCollapsedNavGroups();
  const label = (group: NavGroup) => (group.labelKey ? t(group.labelKey) : group.label);

  return (
    <>
      {groups.map((group, gi) => {
        const activeHere = group.items.some((item) => isNavItemActive(item, pathname));
        const open = !group.label || activeHere || !collapsed.has(group.id);
        const listId = `${idPrefix}-${group.id}`;
        return (
          <React.Fragment key={group.id}>
            {group.label && activeHere ? (
              // Not a control while it cannot fold: a live-looking toggle
              // that does nothing is worse than a plain label.
              <p className={GROUP_LABEL}>
                <span className="flex-1 truncate">{label(group)}</span>
              </p>
            ) : group.label ? (
              <button
                type="button"
                onClick={() => toggle(group.id)}
                aria-expanded={open}
                aria-controls={listId}
                className={`${GROUP_LABEL} rounded-md transition-colors hover:text-[var(--color-text-secondary)]`}
              >
                <span className="flex-1 truncate text-left">{label(group)}</span>
                {open ? (
                  <ChevronDown aria-hidden className="h-3 w-3 shrink-0 opacity-60" />
                ) : (
                  <ChevronRight aria-hidden className="h-3 w-3 shrink-0 opacity-60" />
                )}
              </button>
            ) : gi > 0 ? (
              <div className="pt-1.5" />
            ) : null}
            {open && (
              <div id={listId} className="space-y-0.5">
                {group.items.map((item) => (
                  <React.Fragment key={item.href}>
                    {renderItem(item, isNavItemActive(item, pathname))}
                  </React.Fragment>
                ))}
              </div>
            )}
          </React.Fragment>
        );
      })}
    </>
  );
}
