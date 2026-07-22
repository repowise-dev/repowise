"use client";

import * as React from "react";
import { useEffect, useMemo, useState } from "react";
import { Search, X } from "lucide-react";
import { CouplingGraph } from "./coupling-graph";
import { CouplingTable } from "./coupling-table";
import { GraphCanvasShell } from "../graph/graph-canvas-shell";
import { AiPromptModal, buildCouplingAiPrompt } from "../health";
import { fileEntityPath } from "../shared/entity/routes";
import { cn } from "../lib/cn";
import type { CouplingEdge, CouplingGraphResponse } from "@repowise-dev/types/coupling";

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
  /** Link component for file links (defaults to a plain anchor). */
  LinkComponent?: LinkLike;
  /**
   * Initial pinned file (e.g. from a `?focus=` deep link). When omitted the
   * explorer opens with the most-coupled hub pinned so the diagram lands with a
   * story already told rather than a flat ring.
   */
  initialFocus?: string | null;
  /** Called when the user pins/clears a file — reflect it to the URL if wanted. */
  onFocusChange?: (path: string | null) => void;
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

function shortName(path: string): string {
  return path.split("/").pop() ?? path;
}

/**
 * The full change-coupling surface: the edge-bundling diagram (centerpiece)
 * over a precise, sortable, filterable table, sharing one focus model. A
 * transient `hover` peeks over a sticky `pinned` selection so the picture never
 * goes blank — hover a file (in either the ring or the table) to trace what it
 * changes with, click to pin, click empty canvas to clear.
 *
 * This composition lives in the shared package (not the app) so both the OSS
 * and hosted apps get the same interaction from a single source; the app only
 * supplies the link prefix and, optionally, URL sync for the pinned file.
 */
export function CouplingExplorer({
  data,
  repoLinkPrefix,
  LinkComponent,
  initialFocus,
  onFocusChange,
}: CouplingExplorerProps) {
  const defaultPin = useMemo(
    () => (initialFocus !== undefined ? initialFocus : topHub(data.edges)),
    // Only seed once from the initial data/prop; user actions drive it after.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );
  const [pinned, setPinned] = useState<string | null>(defaultPin);
  const [hover, setHover] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [promptEdge, setPromptEdge] = useState<CouplingEdge | null>(null);

  // Drop a stale pin if a background revalidation returns an edge set that no
  // longer contains it, so the guidance never claims to trace a vanished file.
  const nodePaths = useMemo(
    () => new Set(data.nodes.map((n) => n.file_path)),
    [data.nodes],
  );
  useEffect(() => {
    if (pinned && !nodePaths.has(pinned)) setPinned(null);
  }, [pinned, nodePaths]);

  // Transient hover peeks over the sticky pin: what the ring and table light up.
  const focus = hover ?? pinned;

  const changePin = (path: string | null) => {
    setPinned(path);
    onFocusChange?.(path);
  };

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return data.edges;
    return data.edges.filter(
      (e) => e.source.toLowerCase().includes(q) || e.target.toLowerCase().includes(q),
    );
  }, [data.edges, query]);

  const linkForPath = repoLinkPrefix
    ? (path: string) => fileEntityPath(repoLinkPrefix, path)
    : undefined;

  return (
    <div className="space-y-5">
      <GraphCanvasShell className="block h-auto">
        <div className="mx-auto w-full max-w-[820px]">
          {/* Guidance line so the diagram is not a mystery until you touch it.
              Echoes the pinned file so the sticky selection is always named. */}
          <p className="mb-2 text-xs text-[var(--color-text-tertiary)]">
            {pinned ? (
              <>
                Tracing{" "}
                <span className="font-mono text-[var(--color-text-secondary)]" title={pinned}>
                  {shortName(pinned)}
                </span>
                . Hover any file to peek, click to pin, or click empty space to clear.
              </>
            ) : (
              <>Hover a file to trace what changes with it; click to pin the view.</>
            )}
          </p>
          <CouplingGraph
            nodes={data.nodes}
            edges={data.edges}
            totalEdges={data.total_edges}
            focusedPath={focus}
            pinnedPath={pinned}
            onHover={setHover}
            onPinToggle={changePin}
          />
        </div>
      </GraphCanvasShell>

      <div className="space-y-3 border-t border-[var(--color-border-default)] pt-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="relative min-w-0 flex-1 sm:max-w-xs">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--color-text-tertiary)]" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter table by file path…"
              aria-label="Filter the coupled-files table by path"
              className="h-8 w-full rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] pl-8 pr-3 text-xs text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-accent-primary)]"
            />
          </div>
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

        <CouplingTable
          edges={filtered}
          focusedPath={focus}
          pinnedPath={pinned}
          onHover={setHover}
          onPinToggle={changePin}
          onGeneratePrompt={setPromptEdge}
          linkForPath={linkForPath}
          LinkComponent={LinkComponent}
        />
      </div>

      <AiPromptModal
        open={promptEdge !== null}
        onOpenChange={(o) => !o && setPromptEdge(null)}
        getPrompt={
          promptEdge
            ? (flavor) => buildCouplingAiPrompt({ edge: promptEdge, flavor })
            : null
        }
        filePath={promptEdge ? `${promptEdge.source} ↔ ${promptEdge.target}` : null}
        title="AI decouple prompt"
        description="A ready-to-paste prompt that has your AI agent diagnose why these two files change together and propose how to decouple them."
      />
    </div>
  );
}
