"use client";

/**
 * Architecture — `/repos/[id]/architecture`.
 *
 * One destination for "how is this built". Phase 1 of the UX overhaul hosts
 * the former /graph, /c4 and /symbols pages as sub-views behind `?view=`;
 * a later phase merges them into shared chrome (one search, one inspector).
 */

import { use } from "react";
import { useQueryState, parseAsStringLiteral } from "nuqs";
import { Code2 } from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { GraphView } from "@/components/architecture/graph-view";
import { C4View } from "@/components/architecture/c4-view";
import { SymbolTableWrapper as SymbolTable } from "@/components/symbols/symbol-table-wrapper";

const VIEWS = ["graph", "c4", "symbols"] as const;
type ArchView = (typeof VIEWS)[number];

const VIEW_LABELS: Record<ArchView, string> = {
  graph: "Dependency Graph",
  c4: "Knowledge Graph",
  symbols: "Symbols",
};

export default function ArchitecturePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: repoId } = use(params);
  const [view, setView] = useQueryState(
    "view",
    parseAsStringLiteral(VIEWS).withDefault("graph"),
  );

  return (
    <div className="flex h-full flex-col">
      {/* View switcher */}
      <div className="flex shrink-0 items-center gap-1 border-b border-[var(--color-border-default)] px-4 py-2 sm:px-6 overflow-x-auto">
        {VIEWS.map((v) => (
          <button
            key={v}
            onClick={() => void setView(v)}
            className={cn(
              "rounded-md px-3 py-1.5 text-xs font-medium whitespace-nowrap transition-colors",
              view === v
                ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
                : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]",
            )}
          >
            {VIEW_LABELS[v]}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {view === "graph" && <GraphView repoId={repoId} />}
        {view === "c4" && <C4View repoId={repoId} />}
        {view === "symbols" && (
          <div className="max-w-[1600px] space-y-6 p-4 sm:p-6">
            <div>
              <h1 className="mb-1 flex items-center gap-2 text-xl font-semibold text-[var(--color-text-primary)]">
                <Code2 className="h-5 w-5 text-[var(--color-accent-primary)]" />
                Symbol Index
              </h1>
              <p className="text-sm text-[var(--color-text-secondary)]">
                Searchable index of all functions, classes, and exports.
              </p>
            </div>
            <SymbolTable repoId={repoId} />
          </div>
        )}
      </div>
    </div>
  );
}
