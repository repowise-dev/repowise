"use client";

import * as React from "react";
import useSWR from "swr";
import { useRouter } from "next/navigation";
import { ChevronDown, ChevronRight, Network } from "lucide-react";
import { DecisionGraphView } from "@repowise-dev/ui/decisions/decision-graph-view";
import { getDecisionGraph } from "@/lib/api/decisions";

interface DecisionGraphWrapperProps {
  repoId: string;
  /**
   * Graph-first: render the canvas as the primary surface (fetched immediately,
   * no collapse chrome). When false the graph stays a collapsible drill-down.
   */
  primary?: boolean;
}

/**
 * Decision-graph section. As the primary surface it fetches on mount and fills
 * a tall canvas; otherwise it is a collapsible that fetches once expanded.
 * Clicking a node navigates to that decision's detail page.
 */
export function DecisionGraphWrapper({ repoId, primary = false }: DecisionGraphWrapperProps) {
  const router = useRouter();
  const [open, setOpen] = React.useState(primary);

  const { data, isLoading } = useSWR(
    open ? `decision-graph:${repoId}` : null,
    () => getDecisionGraph(repoId),
    { revalidateOnFocus: false },
  );

  if (primary) {
    return (
      <div className="h-[560px] rounded-lg border border-[var(--color-border-default)]">
        <DecisionGraphView
          graph={data}
          isLoading={isLoading}
          onSelectDecision={(id) => router.push(`/repos/${repoId}/decisions/${id}`)}
        />
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-[var(--color-border-default)]">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-4 py-3 text-left text-sm font-medium text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]"
        aria-expanded={open}
      >
        {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        <Network className="h-4 w-4" />
        Decision graph
        <span className="text-xs font-normal text-[var(--color-text-tertiary)]">
          relationships, supersessions &amp; conflicts
        </span>
      </button>
      {open && (
        <div className="border-t border-[var(--color-border-default)] p-3">
          <div className="h-[520px]">
            <DecisionGraphView
              graph={data}
              isLoading={isLoading}
              onSelectDecision={(id) => router.push(`/repos/${repoId}/decisions/${id}`)}
            />
          </div>
        </div>
      )}
    </div>
  );
}
