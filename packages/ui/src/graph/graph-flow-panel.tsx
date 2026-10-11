"use client";

import { memo } from "react";
import { X } from "lucide-react";
import type { ExecutionFlowEntry, ExecutionFlows } from "@repowise-dev/types/graph";
import { isNameMatch, terminationCopy } from "./edge-provenance";

export interface GraphFlowPanelProps {
  flows: ExecutionFlows;
  activeFlowIdx: number | null;
  onSelect: (idx: number) => void;
  onClose: () => void;
  /** Trace nodes the loaded canvas does not hold, for the active flow only. */
  missingCount: number;
}

/** Hops resolved on a name and nothing else. 0 when the index carries no origins. */
function nameMatchHops(flow: ExecutionFlowEntry): number {
  return (flow.trace_via ?? []).filter(isNameMatch).length;
}

const FlowRow = memo(function FlowRow({
  flow,
  idx,
  isActive,
  onSelect,
}: {
  flow: ExecutionFlowEntry;
  idx: number;
  isActive: boolean;
  onSelect: (idx: number) => void;
}) {
  const guessed = nameMatchHops(flow);
  return (
    <button
      onClick={() => onSelect(idx)}
      className={`w-full text-left px-2 py-1.5 rounded-md text-xs transition-colors ${
        isActive
          ? "bg-[var(--color-accent-primary)]/15 text-[var(--color-accent-primary)]"
          : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-overlay)] hover:text-[var(--color-text-primary)]"
      }`}
    >
      <div className="font-mono truncate">{flow.entry_point_name}</div>
      <div className="flex items-center gap-2 mt-0.5 text-[10px] text-[var(--color-text-tertiary)]">
        {/* `depth` is the hop count; `trace.length` was the same number said a
            second way, so the row claimed two figures and had one. */}
        <span className="tabular-nums">{flow.depth} calls</span>
        {guessed > 0 && (
          <span
            className="tabular-nums"
            title={`${guessed} of these hops rests on a matching name alone, with no import, package or type behind it.`}
          >
            {guessed} by name
          </span>
        )}
        {flow.crosses_community && <span>cross-community</span>}
      </div>
    </button>
  );
});

/**
 * The flow list on the graph canvas.
 *
 * Its own component, and memoised, because the canvas it floats over sets
 * state on every pointer move: inline rows there re-parse each flow per frame.
 */
export const GraphFlowPanel = memo(function GraphFlowPanel({
  flows,
  activeFlowIdx,
  onSelect,
  onClose,
  missingCount,
}: GraphFlowPanelProps) {
  const activeFlow = activeFlowIdx === null ? undefined : flows.flows[activeFlowIdx];
  const stop = terminationCopy(activeFlow?.termination);

  return (
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]/95 backdrop-blur-sm shadow-lg shadow-black/20 p-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium text-[var(--color-text-primary)]">
          Execution Flows
        </span>
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] text-[var(--color-text-tertiary)] tabular-nums">
            {flows.flows.length} entry points
          </span>
          {/* Same close affordance as the Path Finder panel above. */}
          <button
            onClick={onClose}
            aria-label="Close"
            title="Close"
            className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
          >
            <X className="w-3 h-3" />
          </button>
        </div>
      </div>

      <div className="space-y-1 max-h-60 overflow-y-auto">
        {flows.flows.map((flow, idx) => (
          <FlowRow
            key={flow.entry_point}
            flow={flow}
            idx={idx}
            isActive={activeFlowIdx === idx}
            onSelect={onSelect}
          />
        ))}
      </div>

      {/* One line at most: this panel floats over the canvas, so height is
          borrowed from the thing being read. The missing-node warning wins
          because it also explains why the highlighted path looks short —
          describing where the trace stopped is misleading while the reader
          cannot see the nodes it stopped at. */}
      {missingCount > 0 ? (
        <p className="mt-2 border-t border-[var(--color-border-default)] pt-2 text-[10px] leading-snug text-[var(--color-warning)]">
          This flow includes {missingCount} node{missingCount === 1 ? "" : "s"} not in the
          loaded view — load more nodes to see the full trace.
        </p>
      ) : (
        stop && (
          <p className="mt-2 border-t border-[var(--color-border-default)] pt-2 text-[10px] leading-snug text-[var(--color-text-tertiary)]">
            {stop.sentence}
          </p>
        )
      )}
    </div>
  );
});
