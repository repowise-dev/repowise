"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { GitFork, History, PanelLeftClose, PanelLeftOpen, Pencil, Pin, Plus, Search, Trash2, Undo2 } from "lucide-react";
import { cn } from "../lib/cn";
import { formatRelativeTime } from "../lib/format";
import type { Conversation } from "@repowise-dev/types/chat";

export interface ConversationHistoryProps {
  conversations: Conversation[] | undefined;
  isLoading?: boolean;
  selectedId?: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void | Promise<void>;
  onNew: () => void;
  onRename?: (id: string, title: string) => void | Promise<void>;
  onPin?: (id: string, pinned: boolean) => void | Promise<void>;
  onFork?: (id: string) => void | Promise<void>;
  undoDelete?: { title: string; onUndo: () => void | Promise<void> };
  variant?: "popover" | "rail";
  /** Enables a compact icon rail for the persistent full-page history. */
  collapsible?: boolean;
  /** Optional host-owned key used to remember the rail preference. */
  railPreferenceKey?: string;
  className?: string;
}

function historyGroup(conversation: Conversation): string {
  if (conversation.pinned) return "Pinned";
  const age = Date.now() - new Date(conversation.updated_at).getTime();
  if (age < 86400000) return "Today";
  if (age < 604800000) return "Previous 7 days";
  return "Earlier";
}

export function ConversationHistory({
  conversations, isLoading = false, selectedId = null, onSelect, onDelete,
  onNew, onRename, onPin, onFork, undoDelete, variant = "popover",
  collapsible = false, railPreferenceKey, className,
}: ConversationHistoryProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<{ id: string; title: string } | null>(null);
  const [railCollapsed, setRailCollapsed] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const closePopover = useCallback(() => {
    setOpen(false);
    window.requestAnimationFrame(() => triggerRef.current?.focus());
  }, []);
  useEffect(() => {
    if (!open || variant === "rail") return;
    panelRef.current?.querySelector<HTMLElement>("input, button")?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closePopover();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [closePopover, open, variant]);
  useEffect(() => {
    if (!railPreferenceKey) return;
    setRailCollapsed(window.localStorage.getItem(railPreferenceKey) === "collapsed");
  }, [railPreferenceKey]);
  const updateRailCollapsed = useCallback((collapsed: boolean) => {
    setRailCollapsed(collapsed);
    if (railPreferenceKey) {
      window.localStorage.setItem(railPreferenceKey, collapsed ? "collapsed" : "expanded");
    }
  }, [railPreferenceKey]);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? conversations?.filter((c) => c.title.toLowerCase().includes(q)) : conversations;
  }, [conversations, query]);
  const groups = useMemo(() => {
    const result = new Map<string, Conversation[]>();
    for (const conversation of filtered ?? []) {
      const key = historyGroup(conversation);
      result.set(key, [...(result.get(key) ?? []), conversation]);
    }
    return result;
  }, [filtered]);

  const panel = (
    <div ref={panelRef} {...(variant === "popover" ? { role: "dialog", "aria-label": "Conversation history" } : {})} className={cn("flex min-h-0 flex-col bg-[var(--color-bg-surface)]", variant === "rail" ? "h-full border-r border-[var(--color-border-default)]" : "max-h-[min(70dvh,560px)] w-80 overflow-hidden rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] shadow-[var(--shadow-lg)]")}>
      <div className="flex items-center gap-1 border-b border-[var(--color-border-default)] p-2">
        <button type="button" onClick={() => { onNew(); closePopover(); }} className="flex min-h-9 min-w-0 flex-1 items-center gap-2 rounded-md px-2 text-xs text-[var(--color-text-primary)] hover:bg-[var(--color-bg-elevated)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"><Plus className="h-3.5 w-3.5 shrink-0" /> <span className="truncate">New conversation</span></button>
        {variant === "rail" && collapsible && <button type="button" onClick={() => updateRailCollapsed(true)} aria-label="Collapse conversation history" title="Collapse history" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"><PanelLeftClose className="h-4 w-4" /></button>}
      </div>
      <label className="m-2 flex min-h-9 items-center gap-2 border-b border-[var(--color-border-default)] px-1"><Search className="h-3.5 w-3.5 text-[var(--color-text-tertiary)]" /><span className="sr-only">Search conversations</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search history" className="min-w-0 flex-1 bg-transparent text-xs text-[var(--color-text-primary)] outline-none placeholder:text-[var(--color-text-tertiary)]" /></label>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {isLoading && !conversations && <p className="py-6 text-center text-xs text-[var(--color-text-tertiary)]">Loading…</p>}
        {conversations?.length === 0 && <p className="py-6 text-center text-xs text-[var(--color-text-tertiary)]">No conversations yet</p>}
        {(conversations?.length ?? 0) > 0 && filtered?.length === 0 && <p className="py-6 text-center text-xs text-[var(--color-text-tertiary)]">No conversations match</p>}
        {[...groups].map(([label, rows]) => (
          <section key={label} aria-label={label}>
            <h3 className="px-2 pb-1 pt-3 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">{label}</h3>
            <div className="border-t border-[var(--color-border-default)]">
              {rows.map((conversation) => (
                <div key={conversation.id} className={cn("group relative border-b border-[var(--color-border-default)]", conversation.id === selectedId && "bg-[var(--color-bg-elevated)]")}>
                  {editing?.id === conversation.id ? (
                    <form className="flex gap-1 p-2" onSubmit={(event) => { event.preventDefault(); const title = editing.title.trim(); if (title) void onRename?.(conversation.id, title); setEditing(null); }}><input autoFocus value={editing.title} onChange={(event) => setEditing({ id: conversation.id, title: event.target.value })} aria-label="Conversation title" className="min-w-0 flex-1 border-b border-[var(--color-accent-primary)] bg-transparent text-xs text-[var(--color-text-primary)] outline-none" /><button className="min-h-8 px-2 text-xs text-[var(--color-text-secondary)]">Save</button></form>
                  ) : (
                    <button type="button" onClick={() => { onSelect(conversation.id); closePopover(); }} className="w-full py-2 pl-2 pr-[7.5rem] text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] sm:pr-2"><span className="block text-xs leading-4 text-[var(--color-text-primary)] [overflow-wrap:anywhere]">{conversation.title}</span><span className="mt-0.5 block truncate font-mono text-[10px] tabular-nums text-[var(--color-text-tertiary)]">{formatRelativeTime(conversation.updated_at)} · {conversation.message_count} msgs</span></button>
                  )}
                  <div className="absolute right-1 top-1 flex items-center justify-end gap-0.5 opacity-70 sm:rounded sm:opacity-0 sm:group-hover:bg-[var(--color-bg-overlay)] sm:group-hover:opacity-100 sm:group-focus-within:bg-[var(--color-bg-overlay)] sm:group-focus-within:opacity-100">
                    {onRename && <button type="button" onClick={() => setEditing({ id: conversation.id, title: conversation.title })} aria-label="Rename conversation" className="flex h-7 w-7 items-center justify-center rounded text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-overlay)] hover:text-[var(--color-text-primary)]"><Pencil className="h-3.5 w-3.5" /></button>}
                    {onPin && <button type="button" onClick={() => void onPin(conversation.id, !conversation.pinned)} aria-label={conversation.pinned ? "Unpin conversation" : "Pin conversation"} className="flex h-7 w-7 items-center justify-center rounded text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-overlay)] hover:text-[var(--color-text-primary)]"><Pin className="h-3.5 w-3.5" /></button>}
                    {onFork && <button type="button" onClick={() => void onFork(conversation.id)} aria-label="Fork conversation" className="flex h-7 w-7 items-center justify-center rounded text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-overlay)] hover:text-[var(--color-text-primary)]"><GitFork className="h-3.5 w-3.5" /></button>}
                    <button type="button" onClick={() => void onDelete(conversation.id)} aria-label="Delete conversation" className="flex h-7 w-7 items-center justify-center rounded text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-overlay)] hover:text-[var(--color-error)]"><Trash2 className="h-3.5 w-3.5" /></button>
                  </div>
                </div>
              ))}
            </div>
          </section>
        ))}
      </div>
      {undoDelete && <div className="flex items-center gap-2 border-t border-[var(--color-border-default)] px-3 py-2 text-xs text-[var(--color-text-secondary)]"><span className="min-w-0 flex-1 truncate">Deleted {undoDelete.title}</span><button type="button" onClick={() => void undoDelete.onUndo()} className="inline-flex min-h-8 items-center gap-1 font-medium text-[var(--color-text-primary)]"><Undo2 className="h-3.5 w-3.5" /> Undo</button></div>}
    </div>
  );

  if (variant === "rail" && collapsible && railCollapsed) return (
    <aside aria-label="Conversation history" data-collapsed="true" className={cn("min-h-0 overflow-hidden", className)}>
      <div className="flex h-full flex-col items-center gap-1 border-r border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-1.5 py-2">
        <button type="button" onClick={() => updateRailCollapsed(false)} aria-label="Expand conversation history" title="Expand history" className="flex h-9 w-9 items-center justify-center rounded-md text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"><PanelLeftOpen className="h-4 w-4" /></button>
        <button type="button" onClick={onNew} aria-label="New conversation" title="New conversation" className="flex h-9 w-9 items-center justify-center rounded-md text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"><Plus className="h-4 w-4" /></button>
      </div>
    </aside>
  );
  if (variant === "rail") return <aside aria-label="Conversation history" data-collapsed="false" className={cn("min-h-0 overflow-hidden", className)}>{panel}</aside>;
  return <div className={cn("relative", className)}><button ref={triggerRef} type="button" onClick={() => setOpen(!open)} aria-expanded={open} aria-haspopup="dialog" className="flex min-h-9 items-center gap-1 rounded-md px-2 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"><History className="h-3.5 w-3.5" /><span className="sr-only sm:not-sr-only">History</span></button>{open && <><button type="button" aria-label="Close history" className="fixed inset-0 z-[var(--z-dropdown)]" onClick={closePopover} /><div className="absolute left-0 top-full z-[calc(var(--z-dropdown)+1)] mt-1">{panel}</div></>}</div>;
}
