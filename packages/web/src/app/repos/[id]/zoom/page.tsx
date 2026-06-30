"use client";

/**
 * Zoom Map (experimental): `/repos/[id]/zoom`.
 *
 * A continuous-zoom canvas of the system: start at the whole repo and zoom
 * *into* a card to reveal its layers, groups, folders and files, ranked by how
 * the system actually runs. The page wraps the shared `<ZoomCanvas>` with the
 * navigation chrome (breadcrumb, search-to-zoom, detail panel) and keeps the
 * focused node in the URL so a zoom state is shareable. Gated behind
 * `NEXT_PUBLIC_ENABLE_ZOOM_MAP` while it matures alongside the Knowledge Graph.
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
      title="Zoom Map"
      icon={<ScanSearch className="h-5 w-5 text-[var(--color-accent-primary)]" />}
      description="Zoom into the system to reveal how it runs: layers, groups, folders and files, ranked by execution relevance."
      maxWidth="wide"
    >
      <div className="relative flex h-[calc(100vh-13rem)] min-h-[480px] flex-col overflow-hidden rounded-lg border border-[var(--color-border-default)]">
        {zoomMap && !isLoading && (
          <div className="flex items-center justify-between gap-3 border-b border-[var(--color-border-default)] bg-[var(--color-bg-canvas)] px-3 py-2">
            <ZoomBreadcrumb chain={chain} onCrumb={flyTo} />
            <ZoomSearch
              nodes={allNodes}
              onPick={(id) => {
                flyTo(id);
                setSelected(nodeById.get(id) ?? null);
              }}
            />
          </div>
        )}

        <div className="relative flex-1 overflow-hidden">
          {isLoading && (
            <div className="flex h-full items-center justify-center text-sm text-[var(--color-text-secondary)]">
              Building the zoom map…
            </div>
          )}
          {error && !isLoading && (
            <div className="flex h-full items-center justify-center text-sm text-[var(--color-error)]">
              Could not load the zoom map for this repository.
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
              {selected && (
                <ZoomDetailPanel
                  node={selected}
                  onClose={() => setSelected(null)}
                  onZoom={(id) => flyTo(id)}
                />
              )}
            </>
          )}
        </div>
      </div>
    </PageShell>
  );
}
