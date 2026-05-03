"use client";

import { FileText, Search, Route, ArrowRightFromLine, ArrowLeftToLine } from "lucide-react";

interface GraphContextMenuProps {
  x: number;
  y: number;
  nodeId: string;
  isModule: boolean;
  onViewDocs: () => void;
  onExplore: () => void;
  onPathFrom: () => void;
  onPathTo: () => void;
}

export function GraphContextMenu({
  x,
  y,
  nodeId,
  isModule,
  onViewDocs,
  onExplore,
  onPathFrom,
  onPathTo,
}: GraphContextMenuProps) {
  const shortName = nodeId.split("/").pop() ?? nodeId;

  return (
    <div
      className="fixed z-50 min-w-[200px] rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] shadow-xl shadow-black/40 backdrop-blur-md py-1 text-xs"
      style={{ left: x, top: y }}
    >
      <div className="px-3 py-1.5 text-[10px] text-[var(--color-text-tertiary)] font-mono truncate border-b border-[var(--color-border-default)] mb-1">
        {shortName}
      </div>

      <button
        onClick={onViewDocs}
        className="w-full flex items-center gap-2.5 px-3 py-2 text-left text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] transition-colors"
      >
        <FileText className="w-3.5 h-3.5 text-[var(--color-accent-primary)]" />
        View Documentation
      </button>

      <button
        onClick={onExplore}
        className="w-full flex items-center gap-2.5 px-3 py-2 text-left text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] transition-colors"
      >
        <Search className="w-3.5 h-3.5 text-[var(--color-accent-primary)]" />
        {isModule ? "Explore Module" : "Explore Neighborhood"}
      </button>

      <div className="border-t border-[var(--color-border-default)] my-1" />

      <button
        onClick={onPathFrom}
        className="w-full flex items-center gap-2.5 px-3 py-2 text-left text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] transition-colors"
      >
        <ArrowRightFromLine className="w-3.5 h-3.5 text-[var(--color-accent-graph)]" />
        Find paths from here
      </button>

      <button
        onClick={onPathTo}
        className="w-full flex items-center gap-2.5 px-3 py-2 text-left text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] transition-colors"
      >
        <ArrowLeftToLine className="w-3.5 h-3.5 text-[var(--color-accent-graph)]" />
        Find paths to here
      </button>
    </div>
  );
}
