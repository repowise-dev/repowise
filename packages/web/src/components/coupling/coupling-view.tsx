"use client";

import { useState } from "react";
import { CouplingGraph, CouplingTable } from "@repowise-dev/ui/coupling";
import { GraphCanvasShell } from "@repowise-dev/ui/graph/graph-canvas-shell";
import type { CouplingGraphResponse } from "@repowise-dev/types/coupling";

interface CouplingViewProps {
  data: CouplingGraphResponse;
}

/**
 * Hosts the coupling diagram (centerpiece) and the precise ranked table below
 * it, sharing a single `focusedPath` so hovering the ring emphasizes the table
 * rows and clicking a row lifts the ring — one focus model across both.
 *
 * One airy column: an un-boxed diagram on the page background → thin divider →
 * borderless table. No card chrome, no in-diagram header (the surrounding tab
 * hint supplies the one-liner).
 */
export function CouplingView({ data }: CouplingViewProps) {
  const [focusedPath, setFocusedPath] = useState<string | null>(null);

  return (
    <div className="space-y-5">
      <GraphCanvasShell className="block h-auto">
        <div className="mx-auto w-full max-w-[820px]">
          <CouplingGraph
            nodes={data.nodes}
            edges={data.edges}
            totalEdges={data.total_edges}
            focusedPath={focusedPath}
            onFocusChange={setFocusedPath}
          />
        </div>
      </GraphCanvasShell>

      <div className="border-t border-[var(--color-border-default)] pt-4">
        <CouplingTable
          edges={data.edges}
          focusedPath={focusedPath}
          onFocusChange={setFocusedPath}
        />
      </div>
    </div>
  );
}
