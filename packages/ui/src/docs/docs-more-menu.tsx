"use client";

import { useRef, useState, type ElementType, type KeyboardEvent, type ReactNode } from "react";
import { MoreHorizontal, type LucideIcon } from "lucide-react";
import { Spinner } from "../ui/spinner";
import { Popover, PopoverContent, PopoverTrigger } from "../ui/popover";
import { cn } from "../lib/cn";

export interface DocsMenuItem {
  key: string;
  label: string;
  icon?: LucideIcon;
  /** Runs on select; the menu closes first. */
  onSelect?: () => void;
  /** A link item (a download, say) instead of a callback. */
  href?: string;
  download?: boolean;
  disabled?: boolean;
  /** "model" marks optional model work with the model colour. */
  tone?: "model";
  /** Set on a choice within a group; the item renders as a radio item. */
  checked?: boolean;
  /** A link to the view on screen: marked `aria-current="page"`, and the
   *  trigger reads as active so the way back is visible from the bar. */
  current?: boolean;
}

/** Router-aware anchor for in-app link items (Next's `Link`, say). */
export type DocsMenuLinkComponent = ElementType<{
  href: string;
  role: string;
  className?: string;
  onClick?: () => void;
  "aria-current"?: "page";
  children: ReactNode;
}>;

export interface DocsMenuGroup {
  key: string;
  /** Micro-label above the group, for a set of choices. */
  label?: string;
  items: DocsMenuItem[];
  /** Shown only below `sm`, for a control the bar hides on a phone. Put it
   *  last: a hidden first group would leave the next one's divider on top. */
  phoneOnly?: boolean;
}

const ITEM_SELECTOR = '[role^="menuitem"]:not([disabled])';

/** Arrow keys, Home and End move focus between items, wrapping at the ends. */
function moveFocus(event: KeyboardEvent<HTMLDivElement>) {
  const keys = ["ArrowDown", "ArrowUp", "Home", "End"];
  if (!keys.includes(event.key)) return;
  // A phone-only group is still in the DOM above `sm`; skip what is not drawn.
  const items = Array.from(
    event.currentTarget.querySelectorAll<HTMLElement>(ITEM_SELECTOR),
  ).filter((el) => el.checkVisibility?.() ?? true);
  if (items.length === 0) return;
  event.preventDefault();
  const at = items.indexOf(document.activeElement as HTMLElement);
  let next = 0;
  if (event.key === "End") next = items.length - 1;
  else if (event.key === "ArrowDown") next = (at + 1) % items.length;
  else if (event.key === "ArrowUp") next = at <= 0 ? items.length - 1 : at - 1;
  items[next]?.focus();
}

const ITEM_CLASS =
  "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs transition-colors hover:bg-[var(--color-bg-elevated)] focus-visible:bg-[var(--color-bg-elevated)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-[var(--color-focus-ring)] disabled:pointer-events-none disabled:opacity-50";

/** Base, tone and selected-state classes for one item. */
function itemClass(item: DocsMenuItem): string {
  const tone =
    item.tone === "model"
      ? "text-[var(--color-model)] hover:text-[var(--color-model-hover)]"
      : "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]";
  const selected = item.current || item.checked;
  return cn(ITEM_CLASS, tone, selected && "font-medium text-[var(--color-text-primary)]");
}

function ItemContent({ item }: { item: DocsMenuItem }) {
  const Icon = item.icon;
  return (
    <>
      {Icon ? <Icon aria-hidden className="h-3.5 w-3.5 shrink-0" /> : null}
      <span className="min-w-0 flex-1 truncate">{item.label}</span>
    </>
  );
}

/** A link item: router-aware for an in-app view, a plain anchor for a download. */
function LinkItem({
  item,
  href,
  close,
  LinkComponent,
}: {
  item: DocsMenuItem;
  href: string;
  close: () => void;
  LinkComponent?: DocsMenuLinkComponent | undefined;
}) {
  const shared = {
    role: "menuitem",
    href,
    className: itemClass(item),
    onClick: close,
    ...(item.current ? { "aria-current": "page" as const } : {}),
  };
  const Anchor: DocsMenuLinkComponent =
    LinkComponent && !item.download ? LinkComponent : "a";
  return (
    <Anchor {...shared} {...(item.download ? { download: true } : {})}>
      <ItemContent item={item} />
    </Anchor>
  );
}

function MenuItem({
  item,
  close,
  LinkComponent,
}: {
  item: DocsMenuItem;
  close: (selected?: boolean) => void;
  LinkComponent?: DocsMenuLinkComponent | undefined;
}) {
  if (item.href) {
    return (
      <LinkItem item={item} href={item.href} close={() => close()} LinkComponent={LinkComponent} />
    );
  }
  const isChoice = item.checked !== undefined;
  return (
    <button
      type="button"
      role={isChoice ? "menuitemradio" : "menuitem"}
      {...(isChoice ? { "aria-checked": item.checked } : {})}
      disabled={item.disabled}
      className={itemClass(item)}
      onClick={() => {
        close(true);
        item.onSelect?.();
      }}
    >
      <ItemContent item={item} />
    </button>
  );
}

/**
 * One overflow menu for the docs bar. Everything that is not reading or
 * searching lives here, so the bar carries one trigger instead of a row of
 * buttons. The host builds the groups (and translates their labels); this
 * shell only renders them and keeps the keyboard contract: focus lands on the
 * first item, arrows move, Escape closes and returns focus to the trigger.
 *
 * Portalled, so the header's scrolling action row cannot clip it.
 */
export function DocsMoreMenu({
  groups,
  label = "More",
  busy = false,
  LinkComponent,
}: {
  groups: DocsMenuGroup[];
  /** Renders in-app link items; without it they are plain anchors. */
  LinkComponent?: DocsMenuLinkComponent;
  /** Trigger label, and its accessible name on a phone where it is an icon. */
  label?: string;
  /** Shows a spinner on the trigger while a menu action runs (an export). */
  busy?: boolean;
}) {
  const [open, setOpen] = useState(false);
  // Set when an item ran an action: that action may open a dialog, and the
  // popover handing focus back to its trigger would race the dialog's own.
  const selectedRef = useRef(false);
  const visible = groups.filter((g) => g.items.length > 0);
  if (visible.length === 0) return null;
  const current = visible.some((g) => g.items.some((i) => i.current));
  const close = (selected = false) => {
    selectedRef.current = selected;
    setOpen(false);
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-haspopup="menu"
          aria-expanded={open}
          aria-label={label}
          className={cn(
            "flex h-7 shrink-0 items-center gap-1.5 rounded-md border bg-[var(--color-bg-elevated)] px-2 text-xs transition-colors hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)] sm:px-2.5",
            // A view inside the menu is on screen: the trigger takes the
            // active-tab look, so the bar still says where the reader is.
            current
              ? "border-[var(--color-border-hover)] bg-[var(--color-bg-surface)] font-medium text-[var(--color-text-primary)] shadow-sm"
              : "border-[var(--color-border-default)] text-[var(--color-text-secondary)]",
          )}
        >
          {busy ? (
            <Spinner size="sm" />
          ) : (
            <MoreHorizontal aria-hidden className="h-3.5 w-3.5" />
          )}
          <span className="hidden sm:inline">{label}</span>
        </button>
      </PopoverTrigger>
      <PopoverContent
        role="menu"
        aria-label={label}
        align="end"
        className="w-60 p-1"
        onKeyDown={moveFocus}
        onCloseAutoFocus={(e) => {
          if (selectedRef.current) e.preventDefault();
          selectedRef.current = false;
        }}
      >
        {visible.map((group, i) => (
          <div
            key={group.key}
            role="group"
            {...(group.label ? { "aria-label": group.label } : {})}
            className={cn(
              i > 0 && "mt-1 border-t border-[var(--color-border-default)] pt-1",
              group.phoneOnly && "sm:hidden",
            )}
          >
            {group.label ? (
              <p className="px-2 pb-0.5 pt-1 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                {group.label}
              </p>
            ) : null}
            {group.items.map((item) => (
              <MenuItem key={item.key} item={item} close={close} LinkComponent={LinkComponent} />
            ))}
          </div>
        ))}
      </PopoverContent>
    </Popover>
  );
}
