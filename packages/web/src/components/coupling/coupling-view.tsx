"use client";

import { useState } from "react";
import { CouplingGraph, CouplingTable } from "@repowise-dev/ui/coupling";
import type { CouplingGraphResponse } from "@repowise-dev/types/coupling";

interface CouplingViewProps {
  data: CouplingGraphResponse;
}

/**
 * Hosts the coupling diagram (centerpiece) and the precise ranked table below
 * it, sharing a single `focusedPath` so hovering the ring emphasizes the table
 * rows and clicking a row lifts the ring — one focus model across both.
 */
export function CouplingView({ data }: CouplingViewProps) {
  const [focusedPath, setFocusedPath] = useState<string | null>(null);

  return (
    <div className="space-y-5">
      <div className="mx-auto w-full max-w-[820px]">
        <CouplingGraph
          nodes={data.nodes}
          edges={data.edges}
          totalEdges={data.total_edges}
          focusedPath={focusedPath}
          onFocusChange={setFocusedPath}
        />
      </div>
      <div className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-1.5">
        <CouplingTable
          edges={data.edges}
          focusedPath={focusedPath}
          onFocusChange={setFocusedPath}
        />
      </div>
    </div>
  );
}
