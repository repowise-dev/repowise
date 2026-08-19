"use client";

import * as React from "react";
import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";
import { EmptyState } from "../shared/empty-state";
import { VirtualizedTable } from "../shared/virtualized-table";
import { clickableRowProps, CLICKABLE_ROW_CLS } from "../shared/responsive-table";
import { AiPromptButton } from "../health/ai-prompt-button";
import { cn } from "../lib/cn";
import type { CouplingEdge } from "@repowise-dev/types/coupling";

/**
 * Injected link component (e.g. Next's Link); defaults to a plain anchor. Kept
 * to the minimal href/className/children shape so Next's `Link` assigns cleanly
 * (event handlers ride on a wrapper, never on the injected element).
 */
type LinkLike = React.ElementType<{
  href: string;
  className?: string;
  children: React.ReactNode;
}>;

interface CouplingTableProps {
  edges: CouplingEdge[];
  /** Focused file (emphasizes rows incident to it; synced with the diagram). */
  focusedPath?: string | null;
  /** Sticky selection (drives the selected-row style; synced with the diagram). */
  pinnedPath?: string | null;
  /** Transient hover peek — row/filename enter, or table leave (null). */
  onHover?: (path: string | null) => void;
  /** Sticky selection toggle — clicking a row pins/unpins its source file. */
  onPinToggle?: (path: string | null) => void;
  /** When set, each row shows an "AI decouple prompt" action. */
  onGeneratePrompt?: (edge: CouplingEdge) => void;
  /** Resolve a file's detail-page href; when set, file names become links. */
  linkForPath?: ((path: string) => string) | undefined;
  /** Link component used for file links (defaults to a plain anchor). */
  LinkComponent?: LinkLike | undefined;
}

type SortKey = "strength" | "last";
type SortDir = "asc" | "desc";

function basename(path: string): string {
  return path.split("/").pop() ?? path;
}

/** A clickable column header that toggles/shows the active sort direction. */
function SortHeader({
  label,
  columnKey,
  sortKey,
  sortDir,
  onToggle,
}: {
  label: React.ReactNode;
  columnKey: SortKey;
  sortKey: SortKey;
  sortDir: SortDir;
  onToggle: (key: SortKey) => void;
}) {
  const active = sortKey === columnKey;
  return (
    <button
      type="button"
      onClick={() => onToggle(columnKey)}
      aria-sort={active ? (sortDir === "asc" ? "ascending" : "descending") : "none"}
      className="inline-flex items-center gap-1 font-medium uppercase tracking-wider hover:text-[var(--color-text-secondary)]"
    >
      {label}
      {active ? (
        sortDir === "asc" ? (
          <ArrowUp className="h-3 w-3" />
        ) : (
          <ArrowDown className="h-3 w-3" />
        )
      ) : (
        // Reserve the arrow slot so the label doesn't shift when it activates.
        <span className="inline-block h-3 w-3" />
      )}
    </button>
  );
}

// Column-priority hide classes, mirroring the shared ResponsiveTable scale:
// priority 2 hides below md (768px), priority 3 hides below lg (1024px). The
// "pair" identity column (priority 1) is always visible.
const HIDE_BELOW_MD = "max-md:hidden";
const HIDE_BELOW_LG = "max-lg:hidden";

/**
 * The precise, sortable companion to the coupling diagram: one row per
 * coupling. Clicking a row pins its source file in the ring; the two file
 * names are links to their detail pages, and hovering a row (or a name) peeks
 * that file's couplings in the diagram. Rows touching the focused file are
 * emphasized.
 *
 * The body is virtualized (windowed `<tbody>`) so long coupling lists stay
 * cheap to render; below the wrapper's threshold every row renders, so the
 * common short list behaves exactly as a plain table.
 */
export function CouplingTable({
  edges,
  focusedPath,
  pinnedPath,
  onHover,
  onPinToggle,
  onGeneratePrompt,
  linkForPath,
  LinkComponent,
}: CouplingTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>("strength");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  // The strength bars are normalized to the strongest coupling; recompute only
  // when the edge list changes rather than on every render.
  const maxStrength = useMemo(() => Math.max(...edges.map((e) => e.strength), 1), [edges]);

  const sorted = useMemo(() => {
    const dir = sortDir === "asc" ? 1 : -1;
    const val = (e: CouplingEdge) =>
      sortKey === "strength" ? e.strength : e.last_co_change ? Date.parse(e.last_co_change) : 0;
    // Copy before sorting: never mutate the caller's edge array in place.
    return [...edges].sort((a, b) => (val(a) - val(b)) * dir);
  }, [edges, sortKey, sortDir]);

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  };

  const incident = (e: CouplingEdge) =>
    focusedPath != null && (e.source === focusedPath || e.target === focusedPath);

  const Anchor: LinkLike = LinkComponent ?? "a";

  const fileCell = (path: string, e: CouplingEdge, prefix = "") => {
    const hot = incident(e) && path === focusedPath;
    const cls = cn(
      "block truncate font-mono text-xs",
      hot
        ? "text-[var(--color-text-primary)] font-medium"
        : "text-[var(--color-text-secondary)]",
    );
    if (linkForPath) {
      // Handlers ride on the wrapper (not the injected Link): navigate without
      // toggling the row's pin, and peek this exact file (source or target) in
      // the ring on hover.
      return (
        <span
          className="block min-w-0"
          title={path}
          onClick={(ev) => ev.stopPropagation()}
          onMouseEnter={() => onHover?.(path)}
        >
          <Anchor
            href={linkForPath(path)}
            className={cn(cls, "hover:text-[var(--color-accent-primary)] hover:underline")}
          >
            {prefix}
            {basename(path)}
          </Anchor>
        </span>
      );
    }
    return (
      <span className={cls} title={path}>
        {prefix}
        {basename(path)}
      </span>
    );
  };

  if (edges.length === 0) {
    return (
      <EmptyState
        title="No couplings detected"
        description="No files in this repository have a history of changing together yet."
      />
    );
  }

  const header = (
    <tr className="bg-[var(--color-bg-elevated)] text-[var(--color-text-tertiary)] text-xs uppercase tracking-wider">
      <th className="px-3 py-2 text-left font-medium whitespace-nowrap min-w-[200px]">
        Coupled files
      </th>
      <th className={`px-3 py-2 text-left font-medium whitespace-nowrap w-36 ${HIDE_BELOW_MD}`}>
        <SortHeader
          columnKey="strength"
          sortKey={sortKey}
          sortDir={sortDir}
          onToggle={toggleSort}
          label={
            <span
              title="Recency-weighted count of commits that touched both files. Higher means more or more-recent shared changes. It is not a percentage or a verified dependency."
              className="cursor-help underline decoration-dotted underline-offset-2"
            >
              Strength
            </span>
          }
        />
      </th>
      <th className={`px-3 py-2 text-left font-medium whitespace-nowrap ${HIDE_BELOW_LG}`}>
        <SortHeader
          columnKey="last"
          sortKey={sortKey}
          sortDir={sortDir}
          onToggle={toggleSort}
          label="Last"
        />
      </th>
      {onGeneratePrompt ? (
        <th className={`px-3 py-2 text-left font-medium whitespace-nowrap w-10 ${HIDE_BELOW_LG}`} />
      ) : null}
    </tr>
  );

  const renderRow = (e: CouplingEdge) => {
    const onClick = onPinToggle
      ? () => onPinToggle(pinnedPath === e.source ? null : e.source)
      : undefined;
    const isSelected = pinnedPath != null && pinnedPath === e.source;
    return (
      <tr
        className={cn(
          "border-t border-[var(--color-border-default)] hover:bg-[var(--color-bg-elevated)]",
          isSelected && "bg-[var(--color-accent-muted)]/30",
          onClick && CLICKABLE_ROW_CLS,
        )}
        onMouseEnter={onHover ? () => onHover(e.source) : undefined}
        {...(onClick ? clickableRowProps(onClick) : {})}
      >
        <td className="px-3 py-2 text-left min-w-[200px]">
          <div className="flex flex-col gap-0.5">
            {fileCell(e.source, e)}
            {fileCell(e.target, e, "↔ ")}
          </div>
        </td>
        <td className={`px-3 py-2 text-left ${HIDE_BELOW_MD}`}>
          <div className="flex items-center gap-2 min-w-[100px]">
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
              <div
                className="h-full rounded-full bg-[var(--color-accent-primary)]"
                style={{ width: `${Math.max(6, Math.round((e.strength / maxStrength) * 100))}%` }}
              />
            </div>
            <span className="w-8 text-right text-xs tabular-nums text-[var(--color-text-tertiary)]">
              {e.strength}
            </span>
          </div>
        </td>
        <td className={`px-3 py-2 text-left text-xs text-[var(--color-text-tertiary)] ${HIDE_BELOW_LG}`}>
          <span title={e.last_co_change ? new Date(e.last_co_change).toLocaleString() : undefined}>
            {e.last_co_change ? new Date(e.last_co_change).toLocaleDateString() : "—"}
          </span>
        </td>
        {onGeneratePrompt ? (
          <td className={`px-3 py-2 text-left w-10 ${HIDE_BELOW_LG}`}>
            <AiPromptButton
              variant="icon"
              label="AI decouple prompt"
              onClick={() => onGeneratePrompt(e)}
            />
          </td>
        ) : null}
      </tr>
    );
  };

  return (
    <div onMouseLeave={onHover ? () => onHover(null) : undefined}>
      <VirtualizedTable<CouplingEdge>
        rows={sorted}
        rowKey={(e) => `${e.source}|${e.target}`}
        header={header}
        renderRow={renderRow}
        aria-label="Change-coupling pairs"
      />
    </div>
  );
}
