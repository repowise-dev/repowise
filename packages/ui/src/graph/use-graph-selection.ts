import {
  useCallback,
  useEffect,
  useRef,
  type Dispatch,
  type SetStateAction,
} from "react";
import type Graph from "graphology";
import type { FileNodeData, ModuleNodeData } from "./elk-layout";
import type { SigmaCanvasHandle } from "./sigma/sigma-canvas";
import { UNCLUSTERED_COMMUNITY_ID } from "./sigma/constellation-adapter";
import type { SigmaEdgeAttributes, SigmaNodeAttributes } from "./sigma/types";

type SigmaGraph = Graph<SigmaNodeAttributes, SigmaEdgeAttributes>;

// Resting zoom when easing the camera onto a constellation hub. Looser than the
// default file-node focus (0.15) so the hub *and* its surrounding cluster stay
// visible instead of the disc filling the whole viewport.
export const HUB_FOCUS_RATIO = 0.45;

interface GraphSelectionOptions {
  selectedNodeId: string | null;
  setSelectedNodeId: Dispatch<SetStateAction<string | null>>;
  displayGraph: SigmaGraph | null;
  openCommunityPanel: (cid: number) => void;
  setCommunityPanelId: Dispatch<SetStateAction<number | null>>;
  sigmaRef: { current: SigmaCanvasHandle | null };
  initialSelectedNode: string | null | undefined;
  panToNode: (nodeId: string) => void;
  setPathFrom: Dispatch<SetStateAction<string>>;
  setShowPathFinder: Dispatch<SetStateAction<boolean>>;
  setShowFlows: Dispatch<SetStateAction<boolean>>;
  setActiveFlowIdx: Dispatch<SetStateAction<number | null>>;
  effectiveNodeDataMap: Map<string, FileNodeData>;
  effectiveModuleDataMap: Map<string, ModuleNodeData>;
}

/**
 * Selection upkeep and the handlers that change it: drop a node the redraw
 * removed, apply the host's initial selection and frame it, canvas single-click,
 * and the inspector's navigate / find-path actions.
 *
 * `selectedNodeId` itself stays in GraphFlow. The notify effect and the
 * drill-down handlers read it before `displayGraph` exists, and these effects
 * have to stay after the view-mode reset. Call this hook at that later site
 * so effect order does not change.
 */
export function useGraphSelection(opts: GraphSelectionOptions) {
  const {
    selectedNodeId,
    setSelectedNodeId,
    displayGraph,
    openCommunityPanel,
    setCommunityPanelId,
    sigmaRef,
    initialSelectedNode,
    panToNode,
    setPathFrom,
    setShowPathFinder,
    setShowFlows,
    setActiveFlowIdx,
    effectiveNodeDataMap,
    effectiveModuleDataMap,
  } = opts;

  // Unified grammar — SINGLE CLICK = select + inspect (never structural):
  //   file/module → select (no expansion; drill-down moved to double-click)
  //   hub         → select + focus + open the community panel (NO expand)
  //   core        → no-op
  // Clicking an already-selected node is a no-op (keeps it selected); the two
  // pre-clicks Sigma fires before a double-click therefore can't churn the
  // selection. Deselection happens via stage click or Esc.
  const handleSigmaNodeClick = useCallback(
    (nodeId: string, nodeType: string) => {
      if (nodeType === "core") return;
      if (selectedNodeId === nodeId) return;
      if (nodeType === "hub" && displayGraph?.hasNode(nodeId)) {
        const cid = displayGraph.getNodeAttribute(nodeId, "communityId");
        // The "not grouped" disc is a list, not a community: it opens its panel
        // without being selected, since selection dims everything it does not
        // link to, which is everything.
        if (cid === UNCLUSTERED_COMMUNITY_ID) {
          setCommunityPanelId(cid);
          return;
        }
        if (typeof cid === "number" && cid >= 0) {
          setSelectedNodeId(nodeId);
          sigmaRef.current?.focusNode(nodeId, HUB_FOCUS_RATIO);
          openCommunityPanel(cid);
          return;
        }
      }
      setSelectedNodeId(nodeId);
      // The community panel describes a hub; selecting a file replaces it in
      // the rail rather than outranking the inspector forever.
      setCommunityPanelId(null);
    },
    [selectedNodeId, displayGraph, openCommunityPanel],
  );

  // A rebuild can drop the selected node (module expanded into files) — clear
  // the selection then, or the reducer dims the whole canvas around a ghost.
  useEffect(() => {
    if (selectedNodeId && displayGraph && !displayGraph.hasNode(selectedNodeId)) {
      setSelectedNodeId(null);
    }
  }, [displayGraph, selectedNodeId]);

  const initialNodeApplied = useRef(false);
  useEffect(() => {
    if (initialNodeApplied.current || !initialSelectedNode || !displayGraph) return;
    if (displayGraph.hasNode(initialSelectedNode)) {
      initialNodeApplied.current = true;
      setSelectedNodeId(initialSelectedNode);
      setTimeout(() => panToNode(initialSelectedNode), 300);
    }
  }, [initialSelectedNode, displayGraph, panToNode]);

  const handleInspectNavigate = useCallback((nodeId: string) => {
    setSelectedNodeId(nodeId);
    panToNode(nodeId);
  }, [panToNode]);

  const handleInspectFindPath = useCallback(() => {
    if (selectedNodeId) {
      setPathFrom(selectedNodeId);
      setShowPathFinder(true);
      setShowFlows(false);
      setActiveFlowIdx(null);
    }
  }, [selectedNodeId]);

  const inspectedFile = selectedNodeId
    ? effectiveNodeDataMap.get(selectedNodeId)
    : undefined;
  const inspectedNode = selectedNodeId
    ? (inspectedFile ?? effectiveModuleDataMap.get(selectedNodeId))
    : undefined;

  return {
    handleSigmaNodeClick,
    handleInspectNavigate,
    handleInspectFindPath,
    inspectedFile,
    inspectedNode,
  };
}
