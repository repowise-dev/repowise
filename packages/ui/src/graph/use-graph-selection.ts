import {
  useCallback,
  useEffect,
  useRef,
  type Dispatch,
  type SetStateAction,
} from "react";
import type Graph from "graphology";
import type { FileNodeData, ModuleNodeData } from "./elk-layout";
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
 * Selection upkeep: drop a node the redraw removed, apply the host's initial
 * selection and frame it, and the inspector's navigate / find-path actions.
 *
 * `selectedNodeId` itself stays in GraphFlow. The notify effect and the
 * drill-down handlers read it before `displayGraph` exists, and these effects
 * have to stay after the view-mode reset. Call this hook at that later site
 * so effect order does not change. The canvas click handler stays in
 * GraphFlow; its branching is a separate job.
 */
export function useGraphSelection(opts: GraphSelectionOptions) {
  const {
    selectedNodeId,
    setSelectedNodeId,
    displayGraph,
    initialSelectedNode,
    panToNode,
    setPathFrom,
    setShowPathFinder,
    setShowFlows,
    setActiveFlowIdx,
    effectiveNodeDataMap,
    effectiveModuleDataMap,
  } = opts;

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
    handleInspectNavigate,
    handleInspectFindPath,
    inspectedFile,
    inspectedNode,
  };
}
