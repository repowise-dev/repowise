/**
 * Knowledge Graph webview: the web Knowledge Graph page's continuous-zoom
 * canvas (packages/web app/repos/[id]/knowledge-graph), composed from the same
 * shared pieces. Start at the whole repo and zoom into a card to reveal its
 * layers, groups, folders and files; the breadcrumb follows the focus and the
 * bottom-left controls zoom, fit and step up a level.
 *
 * The web page's detail sheet, search, map key and export are web-only
 * components (they link to file pages and the chat dock), so this panel keeps
 * the canvas, breadcrumb and controls, and selecting a file card opens the
 * file in an editor column instead.
 */

import { useCallback, useMemo, useRef, useState } from "react";
import {
  ZoomCanvas,
  indexRelationsByNode,
  type ZoomCanvasHandle,
  type ZoomMap,
  type ZoomNode,
} from "@repowise-dev/ui/zoom";
import { ZoomBreadcrumb } from "@repowise-dev/ui/zoom/zoom-breadcrumb";
import { ZoomControls } from "@repowise-dev/ui/zoom/zoom-controls";
import { ApiError, OrbLoader, ORB_STATE } from "@repowise-dev/ui/shared";
import type { ViewProps } from "../../runtime/mount";
import { useHostQuery } from "../architecture/use-architecture-data";

/** Same frame the web page draws the canvas in. */
const FRAME =
  "relative min-h-[360px] flex-1 overflow-hidden rounded-lg border border-[var(--color-border-default)]";

export function App({ host, params, refreshToken }: ViewProps<"graph">) {
  const { data: zoomMap, error } = useHostQuery<ZoomMap>(
    "zoom-map",
    () => host.api.zoomMap(),
    refreshToken,
  );

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 px-4 py-3">
      {error && !zoomMap ? (
        <div className="flex flex-1 items-center justify-center p-6">
          <ApiError title="Could not load the knowledge graph." message={error} />
        </div>
      ) : !zoomMap ? (
        <div className={`${FRAME} flex items-center justify-center`}>
          <OrbLoader state={ORB_STATE.page} size={64} label="Building the map" />
        </div>
      ) : (
        <ZoomMapView
          zoomMap={zoomMap}
          initialFocusId={params.selectNode}
          onOpenFile={(path) => host.openFile(path)}
        />
      )}
    </div>
  );
}

function ZoomMapView({
  zoomMap,
  initialFocusId,
  onOpenFile,
}: {
  zoomMap: ZoomMap;
  initialFocusId: string | undefined;
  onOpenFile: (path: string) => void;
}) {
  const canvasRef = useRef<ZoomCanvasHandle | null>(null);
  const [chain, setChain] = useState<ZoomNode[]>([]);
  // Indexed once per map for the hover card, as the web page does.
  const relationsByNode = useMemo(() => indexRelationsByNode(zoomMap), [zoomMap]);
  // Snapshot so a later re-init does not yank the camera back. The open
  // parameter may name a node id or a repo path; zoom ids are not paths, so a
  // path resolves to the node that carries it.
  const initialFocus = useRef(
    initialFocusId
      ? zoomMap.nodes.find((n) => n.id === initialFocusId || n.path === initialFocusId)?.id
      : undefined,
  ).current;

  const flyTo = useCallback((id: string) => {
    canvasRef.current?.flyTo(id);
  }, []);

  const handleSelect = useCallback(
    (node: ZoomNode | null) => {
      if (node?.kind === "file" && node.path) onOpenFile(node.path);
    },
    [onOpenFile],
  );

  return (
    <>
      <header className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2">
        <h1 className="text-[15px] font-semibold text-[var(--color-text-primary)]">
          Knowledge Graph
        </h1>
        <div className="min-w-0 flex-1">
          <ZoomBreadcrumb chain={chain} onCrumb={flyTo} />
        </div>
      </header>
      <div className={FRAME}>
        <ZoomCanvas
          ref={canvasRef}
          data={zoomMap}
          {...(initialFocus ? { initialFocusId: initialFocus } : {})}
          onSelect={handleSelect}
          onFocusChange={setChain}
          relationsByNode={relationsByNode}
          minimapClassName="bottom-14 left-3"
        />
        <ZoomControls canvas={canvasRef} canGoUp={chain.length > 1} />
      </div>
    </>
  );
}
