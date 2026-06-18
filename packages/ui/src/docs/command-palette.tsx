"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Search, CornerDownLeft } from "lucide-react";
import { cn } from "../lib/cn";
import { getPageTypeIcon, getPageTypeLabel } from "../lib/page-types";
import { useDebounce } from "../hooks/use-debounce";
import type { DocPage } from "@repowise-dev/types/docs";

interface CommandPaletteProps {
  pages: DocPage[];
  onSelect: (page: DocPage) => void;
  /** Controlled open state. Omit for self-managed (⌘K only). */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /**
   * Optional server-backed search (semantic / full-text). When provided, its
   * results are merged in *after* strong local title/path matches, so the
   * palette stays instant while also surfacing meaning-based hits that the
   * client-side substring filter can't find. Should resolve to `DocPage`s
   * (the caller maps the search endpoint's hits back to loaded pages).
   */
  searchFn?: (query: string) => Promise<DocPage[]>;
}

interface Hit {
  page: DocPage;
  /** Lower is better. */
  rank: number;
  /** Short context snippet when the match was in the body. */
  snippet?: string;
}

// Title hit ranks above path hit ranks above body hit. A leading match
// (startsWith) beats a contained match within the same tier.
function score(page: DocPage, q: string): Hit | null {
  const title = page.title.toLowerCase();
  const path = (page.target_path || "").toLowerCase();
  if (title.startsWith(q)) return { page, rank: 0 };
  if (title.includes(q)) return { page, rank: 1 };
  if (path.includes(q)) return { page, rank: 2 };
  const body = page.content.toLowerCase();
  const at = body.includes(q) ? body.indexOf(q) : -1;
  if (at !== -1) {
    const start = Math.max(0, at - 40);
    const raw = page.content.slice(start, at + q.length + 40).replace(/\s+/g, " ").trim();
    return { page, rank: 3, snippet: (start > 0 ? "…" : "") + raw + "…" };
  }
  return null;
}

/**
 * ⌘K / Ctrl-K full-text command palette over the already-loaded page list.
 * Searches title, path, and body — no extra request, since the page content
 * is in the list response. (Semantic search is a separate backend feature.)
 */
export function DocsCommandPalette({
  pages,
  onSelect,
  open: controlledOpen,
  onOpenChange,
  searchFn,
}: CommandPaletteProps) {
  const [internalOpen, setInternalOpen] = useState(false);
  const isControlled = controlledOpen !== undefined;
  const open = isControlled ? controlledOpen : internalOpen;
  const setOpen = (next: boolean | ((o: boolean) => boolean)) => {
    const value = typeof next === "function" ? next(open) : next;
    if (isControlled) onOpenChange?.(value);
    else setInternalOpen(value);
  };
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const debounced = useDebounce(query, 120);
  // Stable id base so the input's aria-activedescendant can point at the
  // active option row (rows are keyed by page id, which is unique per hit).
  const listboxId = useId();
  const optionId = (pageId: string) => `${listboxId}-opt-${pageId}`;

  // Global ⌘K / Ctrl-K toggle.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      } else if (e.key === "Escape") {
        setOpen(false);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (open) {
      setActive(0);
      // Focus after the panel mounts.
      requestAnimationFrame(() => inputRef.current?.focus());
    } else {
      setQuery("");
    }
  }, [open]);

  const clientHits = useMemo<Hit[]>(() => {
    const q = debounced.trim().toLowerCase();
    if (q.length < 2) return [];
    const out: Hit[] = [];
    for (const page of pages) {
      const hit = score(page, q);
      if (hit) out.push(hit);
    }
    out.sort((a, b) => a.rank - b.rank || a.page.title.localeCompare(b.page.title));
    return out;
  }, [debounced, pages]);

  // Server-backed (semantic / full-text) results, fetched only when a
  // searchFn is supplied. Failures fall back silently to the client layer.
  const [serverPages, setServerPages] = useState<DocPage[]>([]);
  useEffect(() => {
    const q = debounced.trim();
    if (!searchFn || q.length < 2) {
      setServerPages([]);
      return;
    }
    let cancelled = false;
    searchFn(q)
      .then((r) => {
        if (!cancelled) setServerPages(r);
      })
      .catch(() => {
        if (!cancelled) setServerPages([]);
      });
    return () => {
      cancelled = true;
    };
  }, [debounced, searchFn]);

  // Strong local matches (title/path) lead for instant feel; server hits
  // (which can match by meaning or body across all pages) fill in after,
  // then the remaining weaker local body matches. Deduped by page id.
  const hits = useMemo<Hit[]>(() => {
    if (!searchFn) return clientHits.slice(0, 40);
    const seen = new Set<string>();
    const merged: Hit[] = [];
    for (const h of clientHits) {
      if (h.rank <= 2) {
        merged.push(h);
        seen.add(h.page.id);
      }
    }
    for (const p of serverPages) {
      if (!seen.has(p.id)) {
        merged.push({ page: p, rank: 3 });
        seen.add(p.id);
      }
    }
    for (const h of clientHits) {
      if (!seen.has(h.page.id)) {
        merged.push(h);
        seen.add(h.page.id);
      }
    }
    return merged.slice(0, 40);
  }, [clientHits, serverPages, searchFn]);

  useEffect(() => {
    if (active >= hits.length) setActive(0);
  }, [hits, active]);

  if (!open) return null;

  function choose(page: DocPage) {
    onSelect(page);
    setOpen(false);
  }

  function onInputKey(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, hits.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const hit = hits[active];
      if (hit) choose(hit.page);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 px-4 pt-[12vh] backdrop-blur-sm"
      onClick={() => setOpen(false)}
    >
      <div
        className="w-full max-w-xl overflow-hidden rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Input */}
        <div className="flex items-center gap-2 border-b border-[var(--color-border-default)] px-3 py-2.5">
          <Search className="h-4 w-4 shrink-0 text-[var(--color-text-tertiary)]" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onInputKey}
            placeholder="Search pages by title, path, or content…"
            role="combobox"
            aria-expanded
            aria-controls={listboxId}
            aria-autocomplete="list"
            aria-activedescendant={hits[active] ? optionId(hits[active].page.id) : undefined}
            className="flex-1 bg-transparent text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)] outline-none"
          />
          <kbd className="rounded border border-[var(--color-border-default)] px-1.5 py-0.5 text-[10px] text-[var(--color-text-tertiary)]">
            esc
          </kbd>
        </div>

        {/* Results */}
        <div
          id={listboxId}
          role="listbox"
          aria-label="Search results"
          className="max-h-[50vh] overflow-y-auto p-1.5"
        >
          {debounced.trim().length < 2 ? (
            <p className="px-3 py-6 text-center text-xs text-[var(--color-text-tertiary)]">
              Type to search {pages.length} pages
            </p>
          ) : hits.length === 0 ? (
            <p className="px-3 py-6 text-center text-xs text-[var(--color-text-tertiary)]">
              No pages match “{debounced.trim()}”
            </p>
          ) : (
            hits.map((hit, i) => {
              const Icon = getPageTypeIcon(hit.page.page_type);
              return (
                <button
                  key={hit.page.id}
                  id={optionId(hit.page.id)}
                  role="option"
                  aria-selected={i === active}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => choose(hit.page)}
                  className={cn(
                    "flex w-full items-start gap-2.5 rounded-md px-3 py-2 text-left transition-colors",
                    i === active
                      ? "bg-[var(--color-accent-muted)]"
                      : "hover:bg-[var(--color-bg-elevated)]",
                  )}
                >
                  <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)]" />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2">
                      <span className="truncate text-sm text-[var(--color-text-primary)]">
                        {hit.page.title}
                      </span>
                      <span className="shrink-0 text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
                        {getPageTypeLabel(hit.page.page_type)}
                      </span>
                    </span>
                    {hit.page.target_path && (
                      <span className="block truncate font-mono text-xs text-[var(--color-text-tertiary)]">
                        {hit.page.target_path}
                      </span>
                    )}
                    {hit.snippet && (
                      <span className="mt-0.5 block truncate text-xs text-[var(--color-text-secondary)]">
                        {hit.snippet}
                      </span>
                    )}
                  </span>
                  {i === active && (
                    <CornerDownLeft className="mt-0.5 h-3 w-3 shrink-0 text-[var(--color-text-tertiary)]" />
                  )}
                </button>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
