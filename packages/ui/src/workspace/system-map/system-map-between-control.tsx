"use client";

/**
 * The Between picker in the map's title row: a From and a To service, built
 * on the shared `PathFinderPanel` with the loaded graph as its search and
 * path source. A pair joined within two hops in the families shown opens
 * the Between level; otherwise the panel says no route joins them. The host
 * may open it pre-filled (the drawer's "Routes to..." verb).
 */

import { useCallback } from "react";
import { Route } from "lucide-react";
import { PathFinderPanel, type PathFinderOptionDescriber } from "../../graph/path-finder-panel";
import { Popover, PopoverContent, PopoverTrigger } from "../../ui/popover";
import { matchServices, pickerPath } from "./between-picker";
import type { EdgeFamily } from "./edge-families";
import type { LevelsGraph } from "./level-types";

export interface SystemMapBetweenControlProps {
  graph: LevelsGraph;
  families: ReadonlySet<EdgeFamily>;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Pre-filled From service id. */
  from?: string | undefined;
  onBetween: (a: string, b: string) => void;
}

const COPY = {
  title: "Routes between two services",
  fromPlaceholder: "From service...",
  toPlaceholder: "To service...",
  topHeading: "Most linked services",
  submit: "Show routes",
  noPath: "No route joins these services within two hops in the links shown.",
};

export function SystemMapBetweenControl({ graph, families, open, onOpenChange, from, onBetween }: SystemMapBetweenControlProps) {
  const searchNodes = useCallback(async (query: string, limit: number) => matchServices(graph, query, limit), [graph]);
  const findPath = useCallback(async (a: string, b: string) => pickerPath(graph, a, b, families), [graph, families]);
  const describeOption = useCallback<PathFinderOptionDescriber>(
    (r) => ({ label: graph.nodes.find((n) => n.id === r.node_id)?.name ?? r.node_id, detail: r.language }),
    [graph],
  );
  const onPathFound = useCallback(
    (path: string[]) => {
      // The panel shows the reader's order, so the ends are From and To.
      const a = path[0];
      const b = path[path.length - 1];
      if (!a || !b) return;
      onOpenChange(false);
      onBetween(a, b);
    },
    [onBetween, onOpenChange],
  );

  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-expanded={open}
          className="inline-flex h-8 items-center gap-1.5 rounded-md border border-[var(--color-border-hover)] px-2.5 text-xs font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-bg-elevated)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
        >
          <Route size={14} aria-hidden />
          Between
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-auto border-0 bg-transparent p-0 shadow-none">
        <PathFinderPanel
          key={from ?? ""}
          searchNodes={searchNodes}
          findPath={findPath}
          onPathFound={onPathFound}
          onClear={() => {}}
          onClose={() => onOpenChange(false)}
          initialFrom={from ?? ""}
          copy={COPY}
          describeOption={describeOption}
        />
      </PopoverContent>
    </Popover>
  );
}
