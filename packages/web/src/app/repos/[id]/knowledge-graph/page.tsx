"use client";

/**
 * Knowledge Graph: `/repos/[id]/knowledge-graph`.
 *
 * A continuous-zoom canvas of the system: start at the whole repo and zoom
 * *into* a card to reveal its layers, groups, folders and files, ranked by how
 * the system actually runs. The page wraps the shared `<ZoomCanvas>` with the
 * navigation chrome (breadcrumb, search-to-zoom, detail panel, first-visit hint)
 * and keeps the focused node in the URL so a zoom state is shareable.
 *
 * The chrome sits *around* the canvas, not on it. It used to be four floating
 * panels — breadcrumb, search, detail panel, hint — over the one thing on the
 * page that cannot be read past, and two of them shared the top-right corner,
 * so selecting a node dropped the detail panel on top of the search box.
 * Breadcrumb and search are now a header row, the detail panel is a rail, and
 * the canvas keeps the hover card (which tracks the pointer) plus the one-time
 * gesture hint, which teaches the canvas itself and never returns once
 * dismissed. Anything permanent goes around the map, not on it.
 *
 * This replaced the earlier node-link / layered-C4 Knowledge Graph. The reusable
 * C4 machinery it used to render still lives on (backend `c4_builder`, the zoom
 * map's own `/zoom-map` endpoint, and `@repowise-dev/ui/c4` for the VS Code
 * webview); only the old web surface for it was retired.
 */

import { use, useCallback, useMemo, useRef, useState } from "react";
import { parseAsString, useQueryState } from "nuqs";
import { ScanSearch } from "lucide-react";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { ZoomCanvas } from "@repowise-dev/ui/zoom";
import { indexRelationsByNode, summarizeRelations } from "@repowise-dev/ui/zoom";
import type { ZoomCanvasHandle, ZoomNode, ZoomRelation } from "@repowise-dev/ui/zoom";
import { useZoomMap } from "@/lib/hooks/use-graph";
import { ZoomBreadcrumb } from "@/components/zoom/zoom-breadcrumb";
import { ZoomSearch } from "@/components/zoom/zoom-search";
import { ZoomDetailPanel } from "@/components/zoom/zoom-detail-panel";
import { ZoomMapKey } from "@/components/zoom/zoom-map-key";
import { ZoomHint } from "@/components/zoom/zoom-hint";
import { ZoomExportButton } from "@/components/zoom/zoom-export-button";

/** Stable identities, so an unselected / unloaded render does not churn props. */
const EMPTY_RELATIONS: ZoomRelation[] = [];
const NO_RELATIONS: Map<string, ZoomRelation[]> = new Map();

export default function KnowledgeGraphPage({ params }: { params: Promise<{ id: string }> }) {
  const { id: repoId } = use(params);

  const { zoomMap, error, isLoading } = useZoomMap(repoId);
  const canvasRef = useRef<ZoomCanvasHandle | null>(null);

  const [focusParam, setFocusParam] = useQueryState(
    "focus",
    parseAsString.withOptions({ history: "replace", shallow: true }),
  );
  const [chain, setChain] = useState<ZoomNode[]>([]);
  const [selected, setSelected] = useState<ZoomNode | null>(null);
  const [relationVerbs, setRelationVerbs] = useState<ReadonlySet<string> | null>(null);

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
  // Indexed once per map rather than per selection: this repo ships 3,694
  // relations and both the rail and the hover card read them.
  const relationsByNode = useMemo(
    () => (zoomMap ? indexRelationsByNode(zoomMap) : NO_RELATIONS),
    [zoomMap],
  );
  // The verb vocabulary of *this* map, so the filter offers what is actually
  // drawable rather than the seven labels the backend can emit.
  const verbs = useMemo(
    () => summarizeRelations(zoomMap?.relations ?? EMPTY_RELATIONS).verbs,
    [zoomMap],
  );
  const showStats = process.env.NODE_ENV === "development";

  return (
    <PageShell
      title="Knowledge Graph"
      icon={<ScanSearch className="h-5 w-5 text-[var(--color-accent-primary)]" />}
      description="Explore your codebase like a map: scroll to zoom, drag to pan, and double-click any card to dive into its layers, folders and files, ranked by how the code actually runs."
      // The export has its own endpoint and does not need the zoom map, but a
      // map that failed to load is the cheapest signal that this repo has
      // nothing indexed to export either.
      actions={<ZoomExportButton repoId={repoId} disabled={isLoading || !!error} />}
      maxWidth="wide"
    >
      {isLoading && (
        <div className="flex h-[520px] items-center justify-center text-sm text-[var(--color-text-secondary)]">
          Building the knowledge graph…
        </div>
      )}
      {error && !isLoading && (
        <div className="flex h-[520px] items-center justify-center text-sm text-[var(--color-error)]">
          Could not load the knowledge graph for this repository.
        </div>
      )}
      {zoomMap && !isLoading && (
        <>
          {/* The one chrome row: where you are, and how to get somewhere. */}
          <div className="mb-3 flex items-start justify-between gap-3 border-b border-[var(--color-border-default)] pb-3">
            <ZoomBreadcrumb chain={chain} onCrumb={flyTo} />
            <ZoomSearch
              nodes={allNodes}
              onPick={(id) => {
                flyTo(id);
                setSelected(nodeById.get(id) ?? null);
              }}
            />
          </div>

          {/* Canvas and rail are peers in a grid, so the panel can never land
              on the map. Below xl the rail relocates under the canvas rather
              than covering it or disappearing. */}
          {/* Sized so the canvas and the key row below it both fit one screen.
              The key row explains the arrows, and the canvas swallows the wheel
              to zoom, so a reader whose pointer is over the map cannot scroll
              down to find it. */}
          <div
            className={`grid h-[calc(100vh-21.5rem)] min-h-[400px] gap-4 ${
              selected ? "xl:grid-cols-[minmax(0,1fr)_320px]" : "grid-cols-1"
            }`}
          >
            <div className="relative min-h-0 overflow-hidden rounded-lg">
              <ZoomCanvas
                ref={canvasRef}
                data={zoomMap}
                initialFocusId={initialFocus}
                onSelect={setSelected}
                onFocusChange={onFocusChange}
                showStats={showStats}
                relationVerbs={relationVerbs}
                relationsByNode={relationsByNode}
              />
              <ZoomHint />
            </div>
            {selected && (
              <div className="min-h-0 self-start xl:max-h-full">
                <ZoomDetailPanel
                  node={selected}
                  repoId={repoId}
                  relations={relationsByNode.get(selected.id) ?? EMPTY_RELATIONS}
                  relationVerbs={relationVerbs}
                  onClose={() => setSelected(null)}
                  onZoom={(id) => flyTo(id)}
                />
              </div>
            )}
          </div>

          <ZoomMapKey
            verbs={verbs}
            totalFiles={zoomMap.total_files}
            unclaimedFiles={zoomMap.unclaimed_files ?? 0}
            selected={relationVerbs}
            onSelectedChange={setRelationVerbs}
          />
        </>
      )}
    </PageShell>
  );
}
