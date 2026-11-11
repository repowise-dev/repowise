"use client";

/**
 * Search-to-zoom: type the name of a layer, group, folder, file, container or
 * external system and the camera flies to it. Searches the already-loaded map
 * client-side through the shared index (`buildSearchIndex`, built once per
 * map), tags each result with its kind and where it sits, and takes "/" from
 * anywhere on the page to focus (the app's own search owns Ctrl/Cmd+K).
 */

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Search, X } from "lucide-react";
import {
  buildSearchIndex,
  searchIndex,
  type SearchEntry,
  type ZoomMap,
} from "@repowise-dev/ui/zoom";
import { useTranslations } from "next-intl";

interface ZoomSearchProps {
  map: ZoomMap;
  onPick: (id: string) => void;
}

const MAX_RESULTS = 10;

type Translate = ReturnType<typeof useTranslations<"zoom.search">>;

/** True when a keystroke belongs to a field the reader is typing in. */
function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    target.tagName === "INPUT" ||
    target.tagName === "TEXTAREA" ||
    target.tagName === "SELECT"
  );
}

/** "/" focuses `input` from anywhere on the page, as in most code hosts. */
function useSlashFocus(input: React.RefObject<HTMLInputElement | null>): void {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
      if (isTypingTarget(e.target)) return;
      e.preventDefault();
      input.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [input]);
}

/** The highlight after an arrow key, wrapping; null for any other key. */
function stepActive(key: string, current: number, count: number): number | null {
  if (key === "ArrowDown") return (current + 1) % count;
  if (key === "ArrowUp") return (current - 1 + count) % count;
  return null;
}

/** Where a result sits, in one line. */
function placeOf(entry: SearchEntry, t: Translate): string {
  if (entry.kind === "external") return t("usedMostBy", { name: entry.crumbs[0] ?? "" });
  if (entry.kind === "container") return entry.path || t("repositoryRoot");
  const c = entry.crumbs;
  // The layer and the nearest two parents say where it is; the middle of a
  // deep chain only pushes them out of the row.
  return c.length > 3 ? [c[0], "…", ...c.slice(-2)].join(" / ") : c.join(" / ");
}

function ResultRow({
  entry,
  id,
  active,
  t,
  onPick,
  onHover,
}: {
  entry: SearchEntry;
  id: string;
  active: boolean;
  t: Translate;
  onPick: () => void;
  onHover: () => void;
}) {
  const place = placeOf(entry, t);
  return (
    <li
      id={id}
      role="option"
      aria-selected={active}
      // onMouseDown (not onClick) so it fires before the input blur.
      onMouseDown={(e) => {
        e.preventDefault();
        onPick();
      }}
      onMouseEnter={onHover}
      className={`flex cursor-pointer flex-col gap-0.5 px-2.5 py-1.5 ${
        active ? "bg-[var(--color-bg-wash-hover)]" : ""
      }`}
    >
      <span className="flex min-w-0 items-baseline gap-2">
        <span className="truncate font-medium text-[var(--color-text-primary)]">{entry.name}</span>
        <span className="ml-auto shrink-0 font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--color-text-tertiary)]">
          {t(`kind.${entry.kind}`)}
        </span>
      </span>
      {place && (
        <span title={entry.path || undefined} className="truncate text-[var(--color-text-tertiary)]">
          {place}
        </span>
      )}
    </li>
  );
}

export function ZoomSearch({ map, onPick }: ZoomSearchProps) {
  const t = useTranslations("zoom.search");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listId = useId();
  useSlashFocus(inputRef);

  const index = useMemo(() => buildSearchIndex(map), [map]);
  const results = useMemo(() => searchIndex(index, query, MAX_RESULTS), [index, query]);
  // Results can shrink under the highlight (a new map while the list is open).
  const current = Math.min(active, Math.max(0, results.length - 1));
  const expanded = open && results.length > 0;

  const pick = (entry: SearchEntry | undefined) => {
    if (!entry) return;
    onPick(entry.targetId);
    setQuery("");
    setOpen(false);
    inputRef.current?.blur();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      setOpen(false);
      inputRef.current?.blur();
      return;
    }
    if (!expanded) return;
    const next = stepActive(e.key, current, results.length);
    if (next !== null) {
      e.preventDefault();
      setActive(next);
    } else if (e.key === "Enter") {
      e.preventDefault();
      pick(results[current]);
    }
  };

  return (
    <div className="relative w-60">
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
          placeholder={t("placeholder")}
          aria-label={t("aria")}
          role="combobox"
          aria-expanded={expanded}
          aria-controls={expanded ? listId : undefined}
          aria-autocomplete="list"
          aria-activedescendant={expanded ? `${listId}-${current}` : undefined}
          className="w-full bg-transparent text-xs text-[var(--color-text-primary)] outline-none placeholder:text-[var(--color-text-muted)]"
        />
        {query ? (
          <button
            type="button"
            onClick={() => {
              setQuery("");
              inputRef.current?.focus();
            }}
            aria-label={t("clear")}
            className="shrink-0 text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        ) : (
          <kbd
            aria-hidden
            title={t("shortcut")}
            className="shrink-0 rounded border border-[var(--color-border-default)] px-1 font-mono text-[10px] leading-4 text-[var(--color-text-tertiary)]"
          >
            /
          </kbd>
        )}
      </div>
      {expanded && (
        <ul
          id={listId}
          role="listbox"
          aria-label={t("results")}
          className="absolute right-0 z-20 mt-1 max-h-80 w-80 max-w-[calc(100vw-2rem)] overflow-auto rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] py-1 text-xs shadow-lg"
        >
          {results.map((entry, i) => (
            <ResultRow
              key={entry.key}
              entry={entry}
              id={`${listId}-${i}`}
              active={i === current}
              t={t}
              onPick={() => pick(entry)}
              onHover={() => setActive(i)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
