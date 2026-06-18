"use client";

import * as React from "react";
import { X } from "lucide-react";

export interface FilePathPickerProps {
  selected: string[];
  onAdd: (paths: string[]) => void;
  onRemove: (path: string) => void;
  /**
   * Typeahead source — resolves a query to candidate paths. Web injects the
   * graph node search; the picker stays free of data dependencies.
   */
  onSearch: (query: string) => Promise<string[]>;
}

/**
 * Chips + typeahead input over the repo's file index. Pasting a multi-line /
 * comma-separated list adds every path at once. Presentational: the search
 * source is injected so this propagates via package bump.
 */
export function FilePathPicker({ selected, onAdd, onRemove, onSearch }: FilePathPickerProps) {
  const [query, setQuery] = React.useState("");
  const [suggestions, setSuggestions] = React.useState<string[]>([]);
  const [openList, setOpenList] = React.useState(false);
  const [activeIndex, setActiveIndex] = React.useState(-1);
  const containerRef = React.useRef<HTMLDivElement>(null);

  // Debounced typeahead over the injected search source.
  React.useEffect(() => {
    if (!query.trim()) {
      setSuggestions([]);
      setOpenList(false);
      return;
    }
    let cancelled = false;
    const t = window.setTimeout(async () => {
      try {
        const results = await onSearch(query.trim());
        if (cancelled) return;
        const paths = results.filter((p) => !selected.includes(p));
        setSuggestions(paths);
        setOpenList(paths.length > 0);
        setActiveIndex(-1);
      } catch {
        if (!cancelled) setSuggestions([]);
      }
    }, 200);
    return () => {
      cancelled = true;
      window.clearTimeout(t);
    };
  }, [query, selected, onSearch]);

  // Close the list when clicking outside.
  React.useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpenList(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const commit = (path: string) => {
    onAdd([path]);
    setQuery("");
    setSuggestions([]);
    setOpenList(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" && suggestions.length > 0) {
      e.preventDefault();
      setOpenList(true);
      setActiveIndex((i) => (i + 1) % suggestions.length);
    } else if (e.key === "ArrowUp" && suggestions.length > 0) {
      e.preventDefault();
      setActiveIndex((i) => (i <= 0 ? suggestions.length - 1 : i - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (openList && activeIndex >= 0 && suggestions[activeIndex]) {
        commit(suggestions[activeIndex]);
      } else if (query.trim()) {
        commit(query.trim());
      }
    } else if (e.key === "Escape") {
      setOpenList(false);
    } else if (e.key === "Backspace" && !query && selected.length > 0) {
      onRemove(selected[selected.length - 1]!);
    }
  };

  const handlePaste = (e: React.ClipboardEvent<HTMLInputElement>) => {
    const text = e.clipboardData.getData("text");
    if (/[\n,]/.test(text)) {
      e.preventDefault();
      onAdd(text.split(/[\n,]+/));
      setQuery("");
    }
  };

  return (
    <div ref={containerRef} className="relative">
      <div
        className="flex flex-wrap items-center gap-1.5 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2 py-1.5 focus-within:ring-1 focus-within:ring-[var(--color-accent-primary)]"
        onClick={() => containerRef.current?.querySelector("input")?.focus()}
      >
        {selected.map((p) => (
          <span
            key={p}
            className="inline-flex items-center gap-1 rounded border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-1.5 py-0.5 text-xs font-mono text-[var(--color-text-primary)]"
            title={p}
          >
            <span className="truncate max-w-[280px]">{p}</span>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onRemove(p);
              }}
              aria-label={`Remove ${p}`}
              className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          onFocus={() => suggestions.length > 0 && setOpenList(true)}
          placeholder={
            selected.length === 0
              ? "src/auth/login.py — type to search, paste a list, Enter to add"
              : "Add another…"
          }
          aria-label="Changed file paths"
          role="combobox"
          aria-expanded={openList}
          aria-controls="impact-file-suggestions"
          aria-autocomplete="list"
          className="min-w-[180px] flex-1 bg-transparent px-1 py-0.5 text-xs font-mono text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)] focus:outline-none"
        />
      </div>
      {openList && suggestions.length > 0 ? (
        <ul
          id="impact-file-suggestions"
          role="listbox"
          className="absolute z-20 mt-1 w-full max-h-56 overflow-y-auto rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] py-1 shadow-lg"
        >
          {suggestions.map((s, i) => (
            <li key={s} role="option" aria-selected={i === activeIndex}>
              <button
                type="button"
                onMouseDown={(e) => {
                  e.preventDefault();
                  commit(s);
                }}
                onMouseEnter={() => setActiveIndex(i)}
                className={`block w-full truncate px-3 py-1.5 text-left text-xs font-mono ${
                  i === activeIndex
                    ? "bg-[var(--color-bg-elevated)] text-[var(--color-text-primary)]"
                    : "text-[var(--color-text-secondary)]"
                }`}
                title={s}
              >
                {s}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
