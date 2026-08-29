"use client";

import * as React from "react";
import { useMemo, useState } from "react";
import { EmptyState } from "../shared/empty-state";
import { ResponsiveTable, type ResponsiveColumn } from "../shared/responsive-table";
import { AiPromptButton } from "../health/ai-prompt-button";
import { cn } from "../lib/cn";
import { disambiguateBasenames, formatDate, formatDateTime } from "../lib/format";
import { couplingClaim, isSamePair, peakConfidence, type CouplingPair } from "./claim";
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
  /** Sticky pair selection — the row-level equivalent of `pinnedPath`. */
  pinnedPair?: CouplingPair | null;
  /** Transient hover peek — row/filename enter, or table leave (null). */
  onHover?: (path: string | null) => void;
  /** Sticky selection toggle — used for the single-file (diagram) selection. */
  onPinToggle?: (path: string | null) => void;
  /**
   * Sticky pair toggle. When provided, clicking a row pins *both* of its files
   * rather than whichever of them sorts first; the host lights the one arc
   * between them. Falls back to `onPinToggle(source)` when absent.
   */
  onPinPair?: (edge: CouplingEdge | null) => void;
  /** Transient pair peek, the hover counterpart of `onPinPair`. */
  onHoverPair?: (edge: CouplingEdge | null) => void;
  /** When set, each row shows an "AI decouple prompt" action. */
  onGeneratePrompt?: (edge: CouplingEdge) => void;
  /** Resolve a file's detail-page href; when set, file names become links. */
  linkForPath?: ((path: string) => string) | undefined;
  /** Link component used for file links (defaults to a plain anchor). */
  LinkComponent?: LinkLike | undefined;
  /** DOM id for the table wrapper, so the diagram can name it as its equivalent. */
  id?: string;
}

type SortKey = "strength" | "last" | "together";
type SortDir = "asc" | "desc";

// Capped against the viewport, not a flat 600px, so the inner scroller is not
// a nested scroll trap on a phone.
const VIRTUALIZE = { maxHeight: "min(600px, 70vh)" };

const STRENGTH_HELP =
  "Recency-weighted count of commits that touched both files. Higher means more or more-recent shared changes. It is not a percentage or a verified dependency.";

const TOGETHER_HELP =
  "Shared commits, and how much of each file's own history they account for. A pair can be one-sided: a file that never changes alone is a stronger finding than two files that both change often.";

/**
 * The precise, sortable companion to the coupling diagram: one row per
 * coupling, led by the claim the numbers actually support rather than by a bare
 * strength score. Clicking a row pins both of its files in the ring; the two
 * file names are links to their detail pages, and hovering a row peeks that
 * pair in the diagram. Rows touching the focused file are emphasized.
 *
 * The default order is the higher of the two directional confidences, not
 * strength: strength ranks "both change a lot" above "never changes without
 * it", and the second is the finding. Strength stays available behind its own
 * sortable header.
 *
 * The body is virtualized (windowed `<tbody>`) so long coupling lists stay
 * cheap to render; below the wrapper's threshold every row renders, so the
 * common short list behaves exactly as a plain table.
 */
export function CouplingTable({
  edges,
  focusedPath,
  pinnedPath,
  pinnedPair,
  onHover,
  onPinToggle,
  onPinPair,
  onHoverPair,
  onGeneratePrompt,
  linkForPath,
  LinkComponent,
  id,
}: CouplingTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>("together");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  // Bars normalize to the strongest coupling. `reduce`, not `Math.max(...)`:
  // the edge list is not capped client-side and a spread of tens of thousands
  // of arguments overflows the call stack.
  const maxStrength = useMemo(() => edges.reduce((m, e) => Math.max(m, e.strength), 1), [edges]);

  const sorted = useMemo(() => {
    const dir = sortDir === "asc" ? 1 : -1;
    const val = (e: CouplingEdge) => {
      if (sortKey === "strength") return e.strength;
      // Confidence first, shared commits only to break a tie: two pairs that
      // are both fully one-sided are ordered by how much history says so.
      if (sortKey === "together") return (peakConfidence(e) ?? 0) * 1e6 + Math.min(e.support, 1e5);
      return e.last_co_change ? Date.parse(e.last_co_change) : 0;
    };
    // Copy before sorting: never mutate the caller's edge array in place.
    return [...edges].sort((a, b) => (val(a) - val(b)) * dir);
  }, [edges, sortKey, sortDir]);

  const toggleSort = (key: string) => {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key as SortKey);
      setSortDir("desc");
    }
  };

  // Both ends of every pair, labelled the way the diagram labels them.
  const labels = useMemo(
    () => disambiguateBasenames(edges.flatMap((e) => [e.source, e.target])),
    [edges],
  );

  const incident = (e: CouplingEdge) =>
    focusedPath != null && (e.source === focusedPath || e.target === focusedPath);

  const Anchor: LinkLike = LinkComponent ?? "a";

  const labelFor = (path: string) => labels.get(path) ?? path;

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
            {labelFor(path)}
          </Anchor>
        </span>
      );
    }
    return (
      <span className={cls} title={path}>
        {prefix}
        {labelFor(path)}
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

  const columns: ResponsiveColumn<CouplingEdge>[] = [
    {
      key: "pair",
      header: "Coupled files",
      priority: 1,
      cellClassName: "min-w-[260px]",
      headerClassName: "min-w-[260px]",
      render: (e) => (
        <div className="flex flex-col gap-0.5">
          {fileCell(e.source, e)}
          {fileCell(e.target, e, "↔ ")}
          {/* Lead with the claim, not the score: what the two shares actually
              say about this pair, and whether the graph explains it. */}
          <p className="mt-0.5 text-[11px] leading-snug text-[var(--color-text-tertiary)]">
            {couplingClaim(e, labelFor)}
          </p>
        </div>
      ),
    },
    {
      key: "together",
      header: (
        <span
          title={TOGETHER_HELP}
          className="cursor-help underline decoration-dotted underline-offset-2"
        >
          Together
        </span>
      ),
      mobileLabel: "Together",
      priority: 1,
      sortable: true,
      headerClassName: "w-32",
      cellClassName: "text-xs tabular-nums",
      render: (e) => {
        if (!e.support) return <span className="text-[var(--color-text-tertiary)]">—</span>;
        const peak = peakConfidence(e);
        return (
          <div className="flex flex-col leading-tight">
            <span>{e.support} commits</span>
            {peak !== null && (
              <span className="text-[var(--color-text-tertiary)]">
                up to {Math.round(peak * 100)}% of one side
              </span>
            )}
          </div>
        );
      },
    },
    {
      key: "strength",
      // Demoted to priority 2: the sentence and the two shares carry the row
      // now, and strength survives as a sort rather than as the headline.
      priority: 2,
      sortable: true,
      mobileLabel: "Strength",
      headerClassName: "w-36",
      header: (
        <span
          title={STRENGTH_HELP}
          className="cursor-help underline decoration-dotted underline-offset-2"
        >
          Strength
        </span>
      ),
      render: (e) => (
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
      ),
      // The bar is meaningless at card width; the number carries it.
      mobileRender: (e) => e.strength,
    },
    {
      key: "last",
      header: "Last",
      mobileLabel: "Last",
      priority: 2,
      sortable: true,
      cellClassName: "text-xs text-[var(--color-text-tertiary)]",
      render: (e) => (
        <span title={e.last_co_change ? formatDateTime(e.last_co_change) : undefined}>
          {e.last_co_change ? formatDate(e.last_co_change) : "—"}
        </span>
      ),
    },
    ...(onGeneratePrompt
      ? [
          {
            key: "prompt",
            header: "",
            priority: 2 as const,
            headerClassName: "w-10",
            cellClassName: "w-10",
            render: (e: CouplingEdge) => (
              <AiPromptButton
                variant="icon"
                label="AI decouple prompt"
                onClick={() => onGeneratePrompt(e)}
              />
            ),
          },
        ]
      : []),
  ];

  const clearHover = onHoverPair ?? onHover;
  // A row selects the whole pair when the host understands pairs; otherwise it
  // keeps the old single-file behaviour so an uncontrolled table still works.
  const rowClick = onPinPair
    ? (e: CouplingEdge) => onPinPair(isSamePair(e, pinnedPair) ? null : e)
    : onPinToggle
      ? (e: CouplingEdge) => onPinToggle(pinnedPath === e.source ? null : e.source)
      : undefined;
  const rowHover = onHoverPair
    ? (e: CouplingEdge) => onHoverPair(e)
    : onHover
      ? (e: CouplingEdge) => onHover(e.source)
      : undefined;

  return (
    <div id={id} onMouseLeave={clearHover ? () => clearHover(null) : undefined}>
      {/* `rowClassName`, not `selectedKey`: a single-file pin selects several
          rows at once, and it matches on either end — a pair is unordered, so
          lighting only the lexicographically-smaller side was arbitrary. */}
      <ResponsiveTable<CouplingEdge>
        columns={columns}
        rows={sorted}
        rowKey={(e) => `${e.source}|${e.target}`}
        rowClassName={(e) =>
          isSamePair(e, pinnedPair) ||
          (pinnedPath != null && (pinnedPath === e.source || pinnedPath === e.target))
            ? "bg-[var(--color-accent-muted)]/30"
            : undefined
        }
        {...(rowClick ? { onRowClick: rowClick } : {})}
        {...(rowHover ? { onRowHover: rowHover } : {})}
        sortField={sortKey}
        sortOrder={sortDir}
        onSort={toggleSort}
        stacked="md"
        virtualize={VIRTUALIZE}
        caption="Change-coupling pairs"
      />
    </div>
  );
}
