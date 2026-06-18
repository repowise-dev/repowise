import { Network } from "lucide-react";
import { EmptyState } from "../shared/empty-state";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { StatGrid, StatTile } from "../shared/stat-grid";
import { truncatePath } from "../lib/format";
import type { FileDetailGraph, FileGraphNeighbor } from "@repowise-dev/types/files";

interface FileGraphTabProps {
  graph: FileDetailGraph | null;
  filePath: string;
  linkPrefix: string;
  /** Build a file-page href for a neighbor file node. */
  fileHref: (path: string) => string;
  /** Build a symbol-page href for a neighbor symbol node. */
  symbolHref: (symbolId: string) => string;
}

function NeighborList({
  title,
  neighbors,
  fileHref,
  symbolHref,
}: {
  title: string;
  neighbors: FileGraphNeighbor[];
  fileHref: (path: string) => string;
  symbolHref: (symbolId: string) => string;
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">
          {title}{" "}
          <span className="text-[10px] font-normal text-[var(--color-text-tertiary)] tabular-nums">
            {neighbors.length}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {neighbors.length === 0 ? (
          <p className="text-xs text-[var(--color-text-tertiary)]">None in the indexed graph.</p>
        ) : (
          <ul className="space-y-2">
            {neighbors.map((n) => {
              const isSymbol = n.node_type === "symbol" || n.node_id.includes("::");
              const href = isSymbol ? symbolHref(n.node_id) : fileHref(n.node_id);
              return (
                <li key={`${n.node_id}-${n.edge_type}`}>
                  <a
                    href={href}
                    className="flex items-center gap-2 -mx-2 px-2 py-1 rounded hover:bg-[var(--color-bg-elevated)] transition-colors"
                  >
                    <span className="font-mono text-xs text-[var(--color-text-primary)] truncate flex-1 min-w-0" title={n.node_id}>
                      {truncatePath(n.node_id, 48)}
                    </span>
                    <span className="text-[10px] text-[var(--color-text-tertiary)] shrink-0">
                      {n.edge_type}
                    </span>
                  </a>
                  {n.imported_names.length > 0 && (
                    <p
                      className="pl-1 text-[10px] text-[var(--color-text-tertiary)] truncate"
                      title={n.imported_names.join(", ")}
                    >
                      {n.imported_names.slice(0, 6).join(", ")}
                      {n.imported_names.length > 6 && ` +${n.imported_names.length - 6}`}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

export function FileGraphTab({ graph, filePath, linkPrefix, fileHref, symbolHref }: FileGraphTabProps) {
  if (!graph) {
    return (
      <EmptyState
        icon={<Network className="h-8 w-8" />}
        title="Not in the dependency graph"
        description="This file isn't part of the indexed graph (it may be excluded or unparsed)."
      />
    );
  }

  return (
    <div className="space-y-4">
      <StatGrid columns={4}>
        <StatTile
          label="PageRank"
          value={`${Math.round(graph.pagerank_percentile)}th pct`}
          hint={graph.pagerank.toFixed(6)}
        />
        <StatTile label="Dependents (in)" value={String(graph.in_degree)} />
        <StatTile label="Dependencies (out)" value={String(graph.out_degree)} />
        <StatTile label="Community" value={graph.community_label ?? `#${graph.community_id}`} />
      </StatGrid>
      <div className="flex flex-wrap gap-3 text-xs">
        <a
          href={`${linkPrefix}/architecture?view=graph&node=${encodeURIComponent(filePath)}`}
          className="text-[var(--color-accent-primary)] hover:underline"
        >
          Show in dependency graph →
        </a>
        <a
          href={`${linkPrefix}/architecture?view=symbols&file=${encodeURIComponent(filePath)}`}
          className="text-[var(--color-accent-primary)] hover:underline"
        >
          View symbols →
        </a>
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <NeighborList
          title="Dependents"
          neighbors={graph.dependents}
          fileHref={fileHref}
          symbolHref={symbolHref}
        />
        <NeighborList
          title="Dependencies"
          neighbors={graph.dependencies}
          fileHref={fileHref}
          symbolHref={symbolHref}
        />
      </div>
    </div>
  );
}
