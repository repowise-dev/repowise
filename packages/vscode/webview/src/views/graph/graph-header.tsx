/** Slim chrome above the graph canvas: repo name plus live node/edge counts. */

export interface GraphHeaderProps {
  repoName: string;
  stats: { nodes: number; edges: number } | null;
}

export function GraphHeader({ repoName, stats }: GraphHeaderProps) {
  return (
    <header className="flex shrink-0 items-center justify-between gap-3 border-b border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-4 py-2">
      <div className="flex min-w-0 items-center gap-2">
        <span className="text-sm font-medium text-[var(--color-text-primary)]">
          Knowledge Graph
        </span>
        <span className="truncate text-xs text-[var(--color-text-tertiary)]">
          {repoName}
        </span>
      </div>
      {stats && (
        <div className="flex shrink-0 items-center gap-3 text-xs text-[var(--color-text-tertiary)]">
          <span>
            <span className="font-medium text-[var(--color-text-secondary)]">
              {stats.nodes.toLocaleString()}
            </span>{" "}
            nodes
          </span>
          <span>
            <span className="font-medium text-[var(--color-text-secondary)]">
              {stats.edges.toLocaleString()}
            </span>{" "}
            edges
          </span>
        </div>
      )}
    </header>
  );
}
