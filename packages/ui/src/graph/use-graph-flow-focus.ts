import { useEffect, useRef, type Dispatch, type SetStateAction } from "react";
import type Graph from "graphology";
import type { ExecutionFlows } from "@repowise-dev/types/graph";
import { traceToEdgeKeys, traceToFileTrace } from "./graph-flow-helpers";
import type { SigmaCanvasHandle } from "./sigma/sigma-canvas";
import type { SigmaEdgeAttributes, SigmaNodeAttributes } from "./sigma/types";

type SigmaGraph = Graph<SigmaNodeAttributes, SigmaEdgeAttributes>;

interface GraphFlowFocusOptions {
  activeFlowIdx: number | null;
  executionFlows: ExecutionFlows | undefined;
  showFlows: boolean;
  displayGraph: SigmaGraph | null;
  sigmaRef: { current: SigmaCanvasHandle | null };
  focusTimerRef: { current: ReturnType<typeof setTimeout> | undefined };
  setHighlightedPath: Dispatch<SetStateAction<Set<string>>>;
  setHighlightedEdges: Dispatch<SetStateAction<Set<string>>>;
}

/**
 * Highlights the selected execution flow and frames its first file.
 * Call this where those two effects sat in GraphFlow, after the module-groups
 * effect, so their order stays the same. `focusTimerRef` stays in GraphFlow
 * because path-finding clears the same timer.
 */
export function useGraphFlowFocus(opts: GraphFlowFocusOptions) {
  const {
    activeFlowIdx,
    executionFlows,
    showFlows,
    displayGraph,
    sigmaRef,
    focusTimerRef,
    setHighlightedPath,
    setHighlightedEdges,
  } = opts;

  // Flow index whose trace head has already been focused, so the deferred
  // re-focus below fires at most once per selection and never re-steers the
  // camera on later graph changes while the same flow stays active.
  const flowFocusedRef = useRef<number | null>(null);
  // Live graph handle for the focus timer (the effect below deliberately
  // keeps displayGraph out of its deps).
  const drawnGraphRef = useRef(displayGraph);
  drawnGraphRef.current = displayGraph;

  // Execution flow highlighting
  useEffect(() => {
    if (activeFlowIdx === null || !executionFlows) {
      if (activeFlowIdx === null && showFlows) {
        setHighlightedPath(new Set());
        setHighlightedEdges(new Set());
      }
      return;
    }
    const flow = executionFlows.flows[activeFlowIdx];
    if (!flow) return;
    const fileTrace = traceToFileTrace(flow.trace);
    setHighlightedPath(new Set(fileTrace));
    setHighlightedEdges(traceToEdgeKeys(fileTrace));

    clearTimeout(focusTimerRef.current);
    focusTimerRef.current = setTimeout(() => {
      focusTimerRef.current = undefined;
      const firstNode = fileTrace[0];
      if (!firstNode) return;
      if (drawnGraphRef.current?.hasNode(firstNode)) {
        flowFocusedRef.current = activeFlowIdx;
        sigmaRef.current?.focusNode(firstNode);
      }
      // Node not loaded yet (module → full jump still fetching): the
      // deferred-focus effect below picks it up once the graph gains it.
    }, 800);
    return () => clearTimeout(focusTimerRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeFlowIdx, executionFlows]);

  // Deferred flow focus: selecting a flow from the module overview kicks off
  // the full-graph fetch, which can land after the 800ms timer above already
  // fired against a graph without the trace head. Focus once when the graph
  // gains the node; while the timer is still pending it stays the fast path.
  useEffect(() => {
    if (activeFlowIdx === null) {
      flowFocusedRef.current = null;
      return;
    }
    if (flowFocusedRef.current === activeFlowIdx) return;
    if (focusTimerRef.current !== undefined) return;
    const trace = executionFlows?.flows[activeFlowIdx]?.trace;
    const firstNode = trace ? traceToFileTrace(trace)[0] : undefined;
    if (!firstNode || !displayGraph?.hasNode(firstNode)) return;
    flowFocusedRef.current = activeFlowIdx;
    sigmaRef.current?.focusNode(firstNode);
  }, [activeFlowIdx, executionFlows, displayGraph]);

  return drawnGraphRef;
}
