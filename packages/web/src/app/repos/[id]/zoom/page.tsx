"use client";

/**
 * Knowledge Graph (zoom map): `/repos/[id]/zoom`.
 *
 * A continuous-zoom canvas of the system: start at the whole repo and zoom
 * *into* a card to reveal its layers, groups, folders and files, ranked by how
 * the system actually runs. The page wraps the shared `<ZoomCanvas>` with the
 * navigation chrome (breadcrumb, search-to-zoom, detail panel, first-visit hint)
 * and keeps the focused node in the URL so a zoom state is shareable. This is the
 * replacement for the old node-link Knowledge Graph; still gated behind
 * `NEXT_PUBLIC_ENABLE_ZOOM_MAP` until it fully takes over that route.
 */

import { use, useCallback, useMemo, useRef, useState } from "react";
import { notFound } from "next/navigation";
import { parseAsString, useQueryState } from "nuqs";
import { ScanSearch } from "lucide-react";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { ZoomCanvas } from "@repowise-dev/ui/zoom";
import type { ZoomCanvasHandle, ZoomNode } from "@repowise-dev/ui/zoom";
import { useZoomMap } from "@/lib/hooks/use-graph";
import { ZoomBreadcrumb } from "@/components/zoom/zoom-breadcrumb";
import { ZoomSearch } from "@/components/zoom/zoom-search";
import { ZoomDetailPanel } from "@/components/zoom/zoom-detail-panel";
import { ZoomHint } from "@/components/zoom/zoom-hint";

const ZOOM_ENABLED = process.env.NEXT_PUBLIC_ENABLE_ZOOM_MAP === "true";

export default function ZoomPage({ params }: { params: Promise<{ id: string }> }) {
  const { id: repoId } = use(params);
  if (!ZOOM_ENABLED) notFound();

  const { zoomMap, error, isLoading } = useZoomMap(repoId);
  const canvasRef = useRef<ZoomCanvasHandle | null>(null);

  const [focusParam, setFocusParam] = useQueryState(
    "focus",
    parseAsString.withOptions({ history: "replace", shallow: true }),
  );
  const [chain, setChain] = useState<ZoomNode[]>([]);
  const [selected, setSelected] = useState<ZoomNode | null>(null);

  // Snapshot the initial URL focus once so later URL writes don't re-trigger a jump.
  const initialFocus = useRef(focusParam ?? undefined).current;

  const flyTo = useCallback((id: string) => {
    canvasRef.current?.flyTo(id);
  }, []);

  const onFocusChange = useCallback(
    (next: ZoomNode[]) => {
      setChain(next);
      const deepest = next[next.length - 1];
      // Keep the URL in step with where the camera has settled (root = no param).
      void setFocusParam(deepest && deepest.parent_id ? deepest.id : null);
    },
    [setFocusParam],
  );

  const allNodes = useMemo(() => zoomMap?.nodes ?? [], [zoomMap]);
  const nodeById = useMemo(() => new Map(allNodes.map((n) => [n.id, n])), [allNodes]);
  const showStats = process.env.NODE_ENV === "development";

  return (
    <PageShell
      title="Knowledge Graph"
      icon={<ScanSearch className="h-5 w-5 text-[var(--color-accent-primary)]" />}
      description="Explore your codebase like a map: scroll to zoom, drag to pan, and double-click any card to dive into its layers, folders and files, ranked by how the code actually runs."
      maxWidth="wide"
    >
      {/* Full-bleed canvas (no framing border): the map blends into the page.
          Breadcrumb + search float over the canvas as an overlay rather than a
          chrome bar, and a first-visit hint teaches the zoom gestures, so the map
          gets the whole area. */}
      <div className="relative h-[calc(100vh-12rem)] min-h-[520px] overflow-hidden rounded-lg">
        {isLoading && (
          <div className="flex h-full items-center justify-center text-sm text-[var(--color-text-secondary)]">
            Building the knowledge graph…
          </div>
        )}
        {error && !isLoading && (
          <div className="flex h-full items-center justify-center text-sm text-[var(--color-error)]">
            Could not load the knowledge graph for this repository.
          </div>
        )}
        {zoomMap && !isLoading && (
          <>
            <ZoomCanvas
              ref={canvasRef}
              data={zoomMap}
              initialFocusId={initialFocus}
              onSelect={setSelected}
              onFocusChange={onFocusChange}
              showStats={showStats}
            />
            <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex items-start justify-between gap-3 p-3">
              <div className="pointer-events-auto rounded-lg border border-[var(--color-border-subtle)] bg-[var(--color-bg-glass)] px-2 py-1 shadow-sm backdrop-blur">
                <ZoomBreadcrumb chain={chain} onCrumb={flyTo} />
              </div>
              <div className="pointer-events-auto">
                <ZoomSearch
                  nodes={allNodes}
                  onPick={(id) => {
                    flyTo(id);
                    setSelected(nodeById.get(id) ?? null);
                  }}
                />
              </div>
            </div>
            {selected && (
              <ZoomDetailPanel
                node={selected}
                repoId={repoId}
                onClose={() => setSelected(null)}
                onZoom={(id) => flyTo(id)}
              />
            )}
            <ZoomHint />
          </>
        )}
      </div>
    </PageShell>
  );
}
