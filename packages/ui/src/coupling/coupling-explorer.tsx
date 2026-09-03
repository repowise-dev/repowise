"use client";

import * as React from "react";
import { useEffect, useMemo, useState } from "react";
import { Search, X } from "lucide-react";
import { CouplingGraph } from "./coupling-graph";
import { CouplingTable } from "./coupling-table";
import { CouplingPairDrawer } from "./coupling-pair-drawer";
import { segmentOf, type CouplingPair, type CouplingSegment } from "./claim";
import { AiPromptModal, buildCouplingAiPrompt } from "../health";
import { fileEntityPath } from "../shared/entity/routes";
import { cn } from "../lib/cn";
import { truncatePath } from "../lib/format";
import type { CouplingEdge, CouplingGraphResponse, CouplingNode } from "@repowise-dev/types/coupling";

// Stable identities so the `?? []` fallbacks do not invalidate a memo on every
// render when the payload really is missing its arrays.
const NO_NODES: CouplingNode[] = [];
const NO_EDGES: CouplingEdge[] = [];

/** DOM id the diagram points at as its keyboard-accessible equivalent. */
const TABLE_ID = "coupling-pairs-table";

/** Injected link component (e.g. Next's Link); defaults to a plain anchor. */
type LinkLike = React.ElementType<{
  href: string;
  className?: string;
  children: React.ReactNode;
}>;

export interface CouplingExplorerProps {
  data: CouplingGraphResponse;
  /**
   * Repo link prefix (e.g. `/repos/abc123`). File names in the table become
   * links under `${prefix}/files/...`. Omit to render plain (non-link) names.
   */
  repoLinkPrefix?: string;
  /** Repo name, for the AI prompt's header line. */
  repoName?: string;
  /** Link component for file links (defaults to a plain anchor). */
  LinkComponent?: LinkLike;
  /**
   * Initial pinned selection (e.g. from a `?focus=` deep link) — either a file
   * path, or two paths joined by `|` for a pinned pair. When omitted the
   * explorer opens with the most-coupled hub pinned so the diagram lands with a
   * story already told rather than a flat ring.
   */
  initialFocus?: string | null;
  /** Called when the user pins/clears — reflect it to the URL if wanted. */
  onFocusChange?: (focus: string | null) => void;
  /**
   * Fetch beyond the current cap. Shown only while the payload is capped
   * (`total_edges` exceeds the edges delivered), so "showing N of M" stops
   * being a dead end.
   */
  onShowMore?: () => void;
  /** Whether a `onShowMore` fetch is in flight. */
  loadingMore?: boolean;
}

/**
 * What is selected. A coupling is about two files and neither of them owns it,
 * so a table row pins the pair; a dot in the ring still pins one file and its
 * whole fan.
 */
type Selection =
  | { kind: "file"; path: string }
  | { kind: "pair"; source: string; target: string };

/**
 * The pair separator in a serialized selection. A path containing it
 * round-trips as a plain file path, since the halves match no known node.
 */
const PAIR_SEP = "|";

function serializeSelection(sel: Selection | null): string | null {
  if (!sel) return null;
  return sel.kind === "file" ? sel.path : `${sel.source}${PAIR_SEP}${sel.target}`;
}

function parseSelection(raw: string, known: ReadonlySet<string>): Selection | null {
  if (!raw) return null;
  const parts = raw.split(PAIR_SEP);
  if (parts.length === 2 && known.has(parts[0]!) && known.has(parts[1]!)) {
    return { kind: "pair", source: parts[0]!, target: parts[1]! };
  }
  return { kind: "file", path: raw };
}

/** Every file a selection depends on, for the stale-pin check. */
function selectionPaths(sel: Selection): string[] {
  return sel.kind === "file" ? [sel.path] : [sel.source, sel.target];
}

/** The file with the most couplings — the most useful default story to open on. */
function topHub(edges: CouplingEdge[]): string | null {
  const degree = new Map<string, number>();
  for (const e of edges) {
    degree.set(e.source, (degree.get(e.source) ?? 0) + 1);
    degree.set(e.target, (degree.get(e.target) ?? 0) + 1);
  }
  let best: string | null = null;
  let bestN = 0;
  for (const [path, n] of degree) {
    if (n > bestN) {
      best = path;
      bestN = n;
    }
  }
  return best;
}

/** The segments, in reading order, with the label each one carries. */
const SEGMENTS: { key: CouplingSegment | "all"; label: string; help: string }[] = [
  {
    key: "unexplained",
    label: "Unexplained",
    help: "Both files are in the dependency graph and nothing joins them. This is the finding.",
  },
  {
    key: "explained",
    label: "Explained",
    help: "An import, type use, framework wiring, or read already connects the two files.",
  },
  {
    key: "outside",
    label: "Outside the graph",
    help: "At least one side can carry no dependency edge: manifests, changelogs, config, docs. Real coupling, but not a code question.",
  },
  { key: "all", label: "All", help: "Every coupling, including any the index never labelled." },
];

/**
 * The full change-coupling surface: the edge-bundling diagram (centerpiece)
 * over a precise, sortable, filterable table, sharing one focus model *and* one
 * filter. A transient `hover` peeks over a sticky `pinned` selection so the
 * picture never goes blank — hover a file (in either the ring or the table) to
 * trace what it changes with, click to pin, click empty space to clear.
 *
 * The structural segment is the page's front door. Unfiltered, the strongest
 * couplings in any repo are release plumbing, which is true and useless, so the
 * default segment is the pairs the dependency graph cannot explain. The segment
 * and the search box both narrow the ring and the table together.
 *
 * This composition lives in the shared package (not the app) so both the OSS
 * and hosted apps get the same interaction from a single source; the app only
 * supplies the link prefix and, optionally, URL sync for the pinned selection.
 */
export function CouplingExplorer({
  data,
  repoLinkPrefix,
  repoName,
  LinkComponent,
  initialFocus,
  onFocusChange,
  onShowMore,
  loadingMore,
}: CouplingExplorerProps) {
  // The wire type declares these required but a snapshot can omit them. The
  // hosted client normalizes too; this covers every dereference below.
  const nodes = data.nodes ?? NO_NODES;
  const edges = data.edges ?? NO_EDGES;

  const nodePaths = useMemo(() => new Set(nodes.map((n) => n.file_path)), [nodes]);

  const [pinned, setPinned] = useState<Selection | null>(() => {
    if (initialFocus !== undefined && initialFocus !== null && initialFocus !== "") {
      return parseSelection(initialFocus, nodePaths);
    }
    const hub = topHub(edges);
    return hub ? { kind: "file", path: hub } : null;
  });
  const [hover, setHover] = useState<Selection | null>(null);
  const [query, setQuery] = useState("");
  const [promptEdge, setPromptEdge] = useState<CouplingEdge | null>(null);
  /** The pair whose detail panel is open. Set alongside the pin by a row click. */
  const [detailEdge, setDetailEdge] = useState<CouplingEdge | null>(null);

  // An index with no structural labels leaves the control nothing to offer,
  // so it hides rather than filtering every segment to zero.
  const counts = useMemo(() => {
    const tally = { unexplained: 0, explained: 0, outside: 0, unlabelled: 0 };
    for (const e of edges) {
      const seg = segmentOf(e);
      if (seg) tally[seg] += 1;
      else tally.unlabelled += 1;
    }
    return tally;
  }, [edges]);
  const segmentsAvailable = counts.unexplained + counts.explained + counts.outside > 0;

  const [segment, setSegment] = useState<CouplingSegment | "all">(() => {
    // Default to the finding, but never open on an empty list: a repo whose
    // couplings are all explained should still show them.
    for (const e of edges) if (segmentOf(e) === "unexplained") return "unexplained";
    return "all";
  });

  const changePin = (next: Selection | null) => {
    setPinned(next);
    onFocusChange?.(serializeSelection(next));
  };

  // Drop a stale pin if a background revalidation returns a payload that no
  // longer contains it, so the guidance never claims to trace a vanished file.
  useEffect(() => {
    if (pinned && !selectionPaths(pinned).every((p) => nodePaths.has(p))) changePin(null);
    // Intentionally keyed on pin + payload only; changePin always closes over
    // the latest onFocusChange.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pinned, nodePaths]);

  // Transient hover peeks over the sticky pin: what the ring and table light up.
  const active = hover ?? pinned;

  const focusedPath = active?.kind === "file" ? active.path : null;
  const focusedPair: CouplingPair | null =
    active?.kind === "pair" ? { source: active.source, target: active.target } : null;
  const pinnedPath = pinned?.kind === "file" ? pinned.path : null;
  const pinnedPair: CouplingPair | null =
    pinned?.kind === "pair" ? { source: pinned.source, target: pinned.target } : null;

  // One filter, both views. `filtered` feeds the ring and the table alike, and
  // the ring's nodes are re-derived from it so a filtered-out file does not
  // linger on the ring as a dot with no arcs.
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return edges.filter((e) => {
      if (segment !== "all" && segmentOf(e) !== segment) return false;
      if (!q) return true;
      return e.source.toLowerCase().includes(q) || e.target.toLowerCase().includes(q);
    });
  }, [edges, query, segment]);

  const filteredNodes = useMemo(() => {
    if (filtered.length === edges.length) return nodes;
    const referenced = new Set<string>();
    for (const e of filtered) {
      referenced.add(e.source);
      referenced.add(e.target);
    }
    return nodes.filter((n) => referenced.has(n.file_path));
  }, [nodes, edges, filtered]);

  const nodeByPath = useMemo(() => {
    const m = new Map<string, CouplingNode>();
    for (const n of nodes) m.set(n.file_path, n);
    return m;
  }, [nodes]);

  // Degree over the *unfiltered* edge set: "couples with 12 files" is a fact
  // about the repo, not about the segment the reader happens to be in.
  const degreeByPath = useMemo(() => {
    const d = new Map<string, number>();
    for (const e of edges) {
      d.set(e.source, (d.get(e.source) ?? 0) + 1);
      d.set(e.target, (d.get(e.target) ?? 0) + 1);
    }
    return d;
  }, [edges]);

  const moduleFor = useMemo(() => {
    const anyModule = nodes.some((n) => n.module);
    return anyModule ? (path: string) => nodeByPath.get(path)?.module ?? null : undefined;
  }, [nodes, nodeByPath]);

  const linkForPath = repoLinkPrefix
    ? (path: string) => fileEntityPath(repoLinkPrefix, path)
    : undefined;

  const segmentCount = (key: CouplingSegment | "all") =>
    key === "all" ? edges.length : counts[key];

  const capped = data.total_edges > edges.length;

  // The span the couplings are drawn across. Both counts are pre-cap, so the
  // clause holds whether or not the edge list was capped; an older index
  // reports neither, and "in this repository" stays true without them.
  const spanClause =
    data.coupled_files > 0 && data.total_files > 0 ? (
      <>
        {" "}
        across{" "}
        <strong className="font-medium">{data.coupled_files.toLocaleString()}</strong> of{" "}
        <strong className="font-medium">{data.total_files.toLocaleString()}</strong> files
        with commit history
      </>
    ) : (
      " in this repository"
    );

  return (
    <div className="space-y-5">
      {/* No GraphCanvasShell: it hosts fixed-height canvases behind an
          `overflow-hidden` body, which clips this intrinsically-sized SVG. */}
      <div className="mx-auto w-full max-w-[820px]">
        {/* Guidance line so the diagram is not a mystery until you touch it.
            Echoes the pinned selection so it is always named. */}
        <p className="mb-2 text-xs text-[var(--color-text-tertiary)]">
          {pinned ? (
            <>
              Tracing{" "}
              {/* Trailing folders kept: a bare basename is ambiguous wherever
                  `mod.rs`/`index.ts`/`__init__.py` conventions apply. */}
              {selectionPaths(pinned).map((path, i) => (
                <React.Fragment key={path}>
                  {i > 0 ? " ↔ " : null}
                  <span
                    className="font-mono break-all text-[var(--color-text-secondary)]"
                    title={path}
                  >
                    {truncatePath(path, pinned.kind === "pair" ? 28 : 44)}
                  </span>
                </React.Fragment>
              ))}
              . Hover any file to peek, tap or click to pin, or tap empty space
              to clear.
            </>
          ) : (
            <>Tap or hover a file to trace what changes with it; click to pin.</>
          )}
        </p>
        {/* No `totalEdges`: one number cannot honestly carry both the cap and
            the active filter. The header line below owns that; the legend
            counts the arcs it drew. */}
        <CouplingGraph
          nodes={filteredNodes}
          edges={filtered}
          focusedPath={focusedPath}
          pinnedPath={pinnedPath}
          focusedPair={focusedPair}
          pinnedPair={pinnedPair}
          tableId={TABLE_ID}
          onHover={(path) => setHover(path ? { kind: "file", path } : null)}
          onPinToggle={(path) => changePin(path ? { kind: "file", path } : null)}
        />
      </div>

      <div className="space-y-3 border-t border-[var(--color-border-default)] pt-4">
        {/* Names the scope the segment counts are counted over, so "All 200"
            and "14,115 couplings" stop looking like a contradiction, and gives
            that total a denominator: the same number over 300 files and over
            3,000 files describes two different repositories. */}
        <p className="text-xs text-[var(--color-text-secondary)]">
          {capped ? (
            <>
              Showing the <strong className="font-medium">{edges.length}</strong> strongest
              of{" "}
              <strong className="font-medium">{data.total_edges.toLocaleString()}</strong>{" "}
              couplings{spanClause}. The counts below describe those {edges.length}.
            </>
          ) : (
            <>
              All <strong className="font-medium">{edges.length.toLocaleString()}</strong>{" "}
              couplings{spanClause}.
            </>
          )}
        </p>

        {segmentsAvailable && (
          <div className="flex flex-wrap items-center gap-2">
            {/* Scrolls rather than wraps on a phone: a segmented control that
                reflows to two rows stops reading as one control. */}
            <div
              role="group"
              aria-label="Filter couplings by what the dependency graph says"
              className="inline-flex max-w-full overflow-x-auto rounded-md border border-[var(--color-border-default)]"
            >
              {SEGMENTS.map((s) => {
                const n = segmentCount(s.key);
                const selected = segment === s.key;
                return (
                  <button
                    key={s.key}
                    type="button"
                    title={s.help}
                    aria-pressed={selected}
                    onClick={() => setSegment(s.key)}
                    className={cn(
                      "h-8 shrink-0 whitespace-nowrap border-r border-[var(--color-border-default)] px-2.5 text-xs last:border-r-0",
                      selected
                        ? "bg-[var(--color-accent-muted)]/40 font-medium text-[var(--color-text-primary)]"
                        : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]",
                    )}
                  >
                    {s.label}{" "}
                    <span className="tabular-nums text-[var(--color-text-tertiary)]">{n}</span>
                  </button>
                );
              })}
            </div>
            {counts.unlabelled > 0 && segment !== "all" && (
              <span className="text-xs text-[var(--color-text-tertiary)]">
                {counts.unlabelled} coupling{counts.unlabelled === 1 ? "" : "s"} predate the
                structural check and appear only under All.
              </span>
            )}
          </div>
        )}

        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="relative min-w-0 flex-1 sm:max-w-xs">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--color-text-tertiary)]" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter by file path…"
              aria-label="Filter the couplings by file path"
              className="h-8 w-full rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] pl-8 pr-3 text-xs text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-accent-primary)]"
            />
          </div>
          <div className="flex items-center gap-2">
            {capped && onShowMore && (
              <button
                type="button"
                onClick={onShowMore}
                disabled={loadingMore}
                className={cn(
                  "inline-flex h-8 shrink-0 items-center rounded-md border border-[var(--color-border-default)] px-2.5 text-xs text-[var(--color-text-secondary)]",
                  "hover:bg-[var(--color-bg-elevated)] disabled:opacity-60",
                )}
              >
                {loadingMore ? "Loading…" : "Load more"}
              </button>
            )}
            {pinned && (
              <button
                type="button"
                onClick={() => changePin(null)}
                className={cn(
                  "inline-flex h-8 shrink-0 items-center gap-1 rounded-md border border-[var(--color-border-default)] px-2.5 text-xs text-[var(--color-text-secondary)]",
                  "hover:bg-[var(--color-bg-elevated)]",
                )}
              >
                <X className="h-3.5 w-3.5" />
                Clear selection
              </button>
            )}
          </div>
        </div>

        <CouplingTable
          id={TABLE_ID}
          edges={filtered}
          focusedPath={focusedPath}
          pinnedPath={pinnedPath}
          pinnedPair={pinnedPair}
          onHover={(path) => setHover(path ? { kind: "file", path } : null)}
          onPinToggle={(path) => changePin(path ? { kind: "file", path } : null)}
          onPinPair={(edge) => {
            // One gesture, both outcomes: the pair lights in the ring and its
            // detail panel opens. Clicking the pinned row again clears both.
            changePin(edge ? { kind: "pair", source: edge.source, target: edge.target } : null);
            setDetailEdge(edge);
          }}
          onHoverPair={(edge) =>
            setHover(edge ? { kind: "pair", source: edge.source, target: edge.target } : null)
          }
          moduleFor={moduleFor}
          hasDetails
          linkForPath={linkForPath}
          LinkComponent={LinkComponent}
        />
      </div>

      <CouplingPairDrawer
        edge={detailEdge}
        onClose={() => setDetailEdge(null)}
        nodeByPath={nodeByPath}
        degreeByPath={degreeByPath}
        linkForPath={linkForPath}
        LinkComponent={LinkComponent}
        onGeneratePrompt={setPromptEdge}
      />

      <AiPromptModal
        open={promptEdge !== null}
        onOpenChange={(o) => !o && setPromptEdge(null)}
        getPrompt={
          promptEdge
            ? (flavor) =>
                buildCouplingAiPrompt({
                  edge: promptEdge,
                  flavor,
                  ...(repoName ? { repoName } : {}),
                  // The facts the page already holds, so the model is not
                  // asked to re-derive what it is being asked to judge.
                  nodes: {
                    [promptEdge.source]: nodeByPath.get(promptEdge.source) ?? {},
                    [promptEdge.target]: nodeByPath.get(promptEdge.target) ?? {},
                  },
                })
            : null
        }
        filePath={promptEdge ? `${promptEdge.source} ↔ ${promptEdge.target}` : null}
        title="AI decouple prompt"
        description="A ready-to-paste prompt that has your AI agent diagnose why these two files change together and propose how to decouple them."
      />
    </div>
  );
}
