"use client";

/**
 * Search-to-zoom: type a file/folder/layer name and the camera flies to it.
 * Searches the already-loaded zoom tree client-side (no request), ranks name
 * matches above path matches, and exposes keyboard navigation. The page passes
 * the node list and the fly callback.
 */

import { useMemo, useRef, useState } from "react";
import { Search, X } from "lucide-react";
import type { ZoomNode } from "@repowise-dev/ui/zoom";

interface ZoomSearchProps {
  nodes: ZoomNode[];
  onPick: (id: string) => void;
}

const MAX_RESULTS = 8;

function rank(node: ZoomNode, q: string): number {
  const name = node.name.toLowerCase();
  const path = node.path.toLowerCase();
  if (name === q) return 0;
  if (name.startsWith(q)) return 1;
  if (name.includes(q)) return 2;
  if (path.includes(q)) return 3;
  return Number.POSITIVE_INFINITY;
}

export function ZoomSearch({ nodes, onPick }: ZoomSearchProps) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (q.length < 2) return [];
    return nodes
      .map((n) => ({ n, r: rank(n, q) }))
      .filter((x) => Number.isFinite(x.r))
      .sort((a, b) => a.r - b.r || a.n.path.length - b.n.path.length)
      .slice(0, MAX_RESULTS)
      .map((x) => x.n);
  }, [nodes, query]);

  const pick = (node: ZoomNode | undefined) => {
    if (!node) return;
    onPick(node.id);
    setQuery("");
    setOpen(false);
    inputRef.current?.blur();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open || results.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(results.length - 1, a + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(0, a - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      pick(results[active]);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div className="relative w-56">
      <div className="flex items-center gap-1.5 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2 py-1">
        <Search className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
            setActive(0);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 120)}
          onKeyDown={onKeyDown}
          placeholder="Search files…"
          aria-label="Search the system map"
          className="w-full bg-transparent text-xs text-[var(--color-text-primary)] outline-none placeholder:text-[var(--color-text-muted)]"
        />
        {query && (
          <button
            type="button"
            onClick={() => {
              setQuery("");
              inputRef.current?.focus();
            }}
            aria-label="Clear search"
            className="shrink-0 text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
      {open && results.length > 0 && (
        <ul className="absolute z-20 mt-1 max-h-72 w-full overflow-auto rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] py-1 text-xs shadow-lg">
          {results.map((node, i) => (
            <li key={node.id}>
              <button
                type="button"
                // onMouseDown (not onClick) so it fires before the input blur.
                onMouseDown={(e) => {
                  e.preventDefault();
                  pick(node);
                }}
                onMouseEnter={() => setActive(i)}
                className={`flex w-full flex-col items-start px-2.5 py-1.5 text-left ${
                  i === active ? "bg-[var(--color-bg-hover)]" : ""
                }`}
              >
                <span className="font-medium text-[var(--color-text-primary)]">{node.name}</span>
                {node.path && node.path !== node.name && (
                  <span className="max-w-full truncate text-[var(--color-text-tertiary)]">
                    {node.path}
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
