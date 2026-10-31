"use client";

/**
 * Knowledge Graph: `/repos/[id]/knowledge-graph`.
 *
 * A continuous-zoom canvas of the system: start at the whole repo and zoom
 * *into* a card to reveal its layers, groups, folders and files, ranked by how
 * the system actually runs. The page wraps the shared `<ZoomCanvas>` with the
 * navigation chrome and keeps the focused node in the URL so a zoom state is
 * shareable.
 *
 * A full-bleed tool: one compact header row (title, where you are, search,
 * export) and the map takes every pixel of height below it. What sits on the
 * map is small, cornered and earns its place: the controls cluster bottom-left
 * (zoom, fit, up, the map key popover, the one-time gesture hint), the minimap
 * above it once you zoom in, and the detail sheet on the right edge while a
 * card is selected.
 *
 * The sheet overlays the canvas instead of taking a grid column: a column would
 * narrow the canvas, re-centre the camera on the new width and slide every card
 * under the pointer, so the next double-click hit a different card. The canvas
 * never resizes for chrome; it pans a selected card clear of the sheet only
 * when the card would sit underneath (`selectionInsetRight`).
 */

import { use, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { parseAsString, useQueryState } from "nuqs";
import { useTranslations } from "next-intl";
import { ScanSearch } from "lucide-react";
import { PageFrame, PAGE_SHELL_TITLE } from "@repowise-dev/ui/shared/page-shell";
import { ZoomCanvas } from "@repowise-dev/ui/zoom";
import { indexRelationsByNode, summarizeRelations, testLayerIds } from "@repowise-dev/ui/zoom";
import type { ZoomCanvasHandle, ZoomNode, ZoomRelation } from "@repowise-dev/ui/zoom";
import { useZoomMap } from "@/lib/hooks/use-graph";
import { ZoomBreadcrumb } from "@/components/zoom/zoom-breadcrumb";
import { ZoomSearch } from "@/components/zoom/zoom-search";
import { ZoomDetailPanel } from "@/components/zoom/zoom-detail-panel";
import { ZoomMapKey } from "@/components/zoom/zoom-map-key";
import { ZoomLinksLegend } from "@/components/zoom/zoom-links-legend";
import { ZoomHint } from "@/components/zoom/zoom-hint";
import { ZoomExportButton } from "@/components/zoom/zoom-export-button";
import { ZoomControls } from "@/components/zoom/zoom-controls";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { ZOOM_FRAME, ZoomCanvasError, ZoomCanvasSkeleton } from "@/components/zoom/zoom-canvas-state";
import { useZoomCanvasLabels } from "@/components/zoom/use-zoom-canvas-labels";
import { ZOOM_SHEET_INSET_PX, ZoomSheet } from "@/components/zoom/zoom-sheet";
import { useZoomC4, ZoomC4Provider } from "@/components/zoom/zoom-detail-c4";

/** Stable identities, so an unselected / unloaded render does not churn props. */
const EMPTY_RELATIONS: ZoomRelation[] = [];
const NO_RELATIONS: Map<string, ZoomRelation[]> = new Map();
/** How long the focus holds still before it is written to the URL. */
const FOCUS_URL_SETTLE_MS = 300;

/**
 * `write`, called only once the value has held still for `FOCUS_URL_SETTLE_MS`:
 * the URL follows the camera once it stops, since focus changes fire for every
 * node a fly passes through and each would be a history write.
 */
function useSettledWrite(write: (id: string | null) => unknown): (id: string | null) => void {
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  return useCallback(
    (id: string | null) => {
      clearTimeout(timer.current);
      timer.current = setTimeout(() => void write(id), FOCUS_URL_SETTLE_MS);
    },
    [write],
  );
}

export default function KnowledgeGraphPage({ params }: { params: Promise<{ id: string }> }) {
  const t = useTranslations("views.knowledgeGraph");
  const { id: repoId } = use(params);

  const { zoomMap, error, isLoading, retry } = useZoomMap(repoId);
  const canvasRef = useRef<ZoomCanvasHandle | null>(null);

  const [focusParam, setFocusParam] = useQueryState(
    "focus",
    parseAsString.withOptions({ history: "replace", shallow: true }),
  );
  const writeFocusParam = useSettledWrite(setFocusParam);
  const [chain, setChain] = useState<ZoomNode[]>([]);
  const [selected, setSelected] = useState<ZoomNode | null>(null);
  const [relationVerbs, setRelationVerbs] = useState<ReadonlySet<string> | null>(null);
  const [container, setContainer] = useState<string | null>(null);
  const [showTestLinks, setShowTestLinks] = useState(false);

  // Snapshot the initial URL focus once so later URL writes don't re-trigger a jump.
  const initialFocus = useRef(focusParam ?? undefined).current;

  const flyTo = useCallback((id: string) => {
    canvasRef.current?.flyTo(id);
  }, []);

  // Selection lives in the canvas; the page asks it to change and hears back
  // through `onSelect`, so the highlighted card and the sheet cannot disagree.
  const select = useCallback((id: string | null) => {
    canvasRef.current?.select(id);
  }, []);
  const closeSheet = useCallback(() => {
    canvasRef.current?.select(null);
    canvasRef.current?.focus();
  }, []);

  const onFocusChange = useCallback(
    (next: ZoomNode[]) => {
      setChain(next);
      const deepest = next[next.length - 1];
      // Root = no param.
      writeFocusParam(deepest && deepest.parent_id ? deepest.id : null);
    },
    [writeFocusParam],
  );

  // Indexed once per map rather than per selection: this repo ships 3,694
  // relations and both the sheet and the hover card read them.
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
  // The toggle only means something when a test layer was set aside.
  const hasTestLayer = useMemo(() => (zoomMap ? testLayerIds(zoomMap).size > 0 : false), [zoomMap]);
  const { c4, containers, highlightIds, hasExternals } = useZoomC4(zoomMap, container);
  const canvasLabels = useZoomCanvasLabels();
  const showStats = process.env.NODE_ENV === "development";
  const ready = zoomMap && !isLoading;

  return (
    <PageFrame maxWidth="wide" className="flex h-full min-h-0 flex-col gap-3 space-y-0">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <h1 className={PAGE_SHELL_TITLE}>
          <span
            aria-hidden
            className="inline-flex shrink-0 text-[var(--color-text-tertiary)] [&_svg]:h-5 [&_svg]:w-5"
          >
            <ScanSearch />
          </span>
          {t("title")}
        </h1>
        <div className="min-w-0 flex-1">
          <ZoomBreadcrumb chain={chain} onCrumb={flyTo} />
        </div>
        {/* Holds the search box's place while the map loads, so the header does not shift. */}
        {isLoading && <Skeleton aria-hidden className="h-[26px] w-60" />}
        {ready && (
          <ZoomSearch
            map={zoomMap}
            onPick={(id) => {
              // Select first: the sheet it opens is what the fly frames beside.
              select(id);
              flyTo(id);
            }}
          />
        )}
        {ready && hasTestLayer && (
          <label className="flex items-center gap-2 text-xs text-[var(--color-text-secondary)]">
            <input
              type="checkbox"
              checked={showTestLinks}
              onChange={(event) => setShowTestLinks(event.target.checked)}
              className="h-4 w-4 rounded border-[var(--color-border-default)] accent-[var(--color-accent-primary)]"
            />
            {t("showTestLinks")}
          </label>
        )}
        {/* The export has its own endpoint and does not need the zoom map, but
            a map that failed to load is the cheapest signal that this repo has
            nothing indexed to export either. */}
        <ZoomExportButton repoId={repoId} disabled={isLoading || !!error} />
      </header>
      {/* Said in full in the map key popover; kept here for screen readers,
          who meet the page before the canvas. */}
      <p className="sr-only">{t("description")}</p>

      {isLoading && <ZoomCanvasSkeleton />}
      {error && !isLoading && <ZoomCanvasError onRetry={() => void retry()} />}
      {ready && (
        <div className={ZOOM_FRAME}>
          <ZoomCanvas
            ref={canvasRef}
            data={zoomMap}
            initialFocusId={initialFocus}
            onSelect={setSelected}
            onFocusChange={onFocusChange}
            showStats={showStats}
            relationVerbs={relationVerbs}
            highlightIds={highlightIds}
            showTestLinks={showTestLinks}
            relationsByNode={relationsByNode}
            selectionInsetRight={ZOOM_SHEET_INSET_PX}
            minimapClassName="bottom-14 left-3"
            labels={canvasLabels}
          />
          <ZoomControls canvas={canvasRef} canGoUp={chain.length > 1}>
            <ZoomMapKey
              about={t("description")}
              verbs={verbs}
              totalFiles={zoomMap.total_files}
              unclaimedFiles={zoomMap.unclaimed_files ?? 0}
              selected={relationVerbs}
              onSelectedChange={setRelationVerbs}
              containers={containers}
              container={container}
              onContainerChange={setContainer}
              hasExternals={hasExternals}
              linksLegend={
                <ZoomLinksLegend
                  map={zoomMap}
                  levelId={chain[chain.length - 1]?.id ?? zoomMap.root_id}
                  relationVerbs={relationVerbs}
                  showTestLinks={showTestLinks}
                />
              }
            />
            <ZoomHint />
          </ZoomControls>
          {selected && (
            <ZoomSheet onClose={closeSheet}>
              <ZoomC4Provider value={c4}>
                <ZoomDetailPanel
                  node={selected}
                  repoId={repoId}
                  relations={relationsByNode.get(selected.id) ?? EMPTY_RELATIONS}
                  relationVerbs={relationVerbs}
                  onClose={closeSheet}
                  onZoom={flyTo}
                />
              </ZoomC4Provider>
            </ZoomSheet>
          )}
        </div>
      )}
    </PageFrame>
  );
}
