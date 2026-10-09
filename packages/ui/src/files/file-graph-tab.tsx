import type { ElementType } from "react";
import { Network } from "lucide-react";
import { EmptyState } from "../shared/empty-state";
import { truncatePath } from "../lib/format";
import type { FileDetailGraph, FileGraphNeighbor } from "@repowise-dev/types/files";
import { isExternal, nodeKind } from "@repowise-dev/types";
import { FileSection, Fig } from "./file-section";

interface FileGraphTabProps {
  graph: FileDetailGraph | null;
  filePath: string;
  linkPrefix: string;
  /** Build a file-page href for a neighbor file node. */
  fileHref: (path: string) => string;
  /** Build a symbol-page href for a neighbor symbol node. */
  symbolHref: (symbolId: string) => string;
  /**
   * Router link. Every href here leaves this page, and a neighbour link is the
   * one that goes file page → file page: a full document load on a surface
   * whose whole point is walking the graph. Defaults to `<a>` so the package
   * stays framework-neutral.
   */
  LinkComponent?: ElementType | undefined;
}

function NeighborList({
  neighbors,
  fileHref,
  symbolHref,
  LinkComponent = "a",
}: {
  neighbors: FileGraphNeighbor[];
  fileHref: (path: string) => string;
  symbolHref: (symbolId: string) => string;
  LinkComponent?: ElementType | undefined;
}) {
  const A = LinkComponent;
  if (neighbors.length === 0) {
    return <p className="text-sm text-[var(--color-text-tertiary)]">None in the indexed graph.</p>;
  }
  return (
    <ul className="divide-y divide-[var(--color-border-default)] border-y border-[var(--color-border-default)]">
      {neighbors.map((n) => {
        const isExternalNode = isExternal(n.node_id);
        const isSymbol = !isExternalNode && nodeKind(n.node_id) === "symbol";
        const href = isSymbol ? symbolHref(n.node_id) : fileHref(n.node_id);
        const row = (
          <>
            <span
              className={`min-w-0 flex-1 truncate font-mono text-xs ${
                isExternalNode
                  ? "text-[var(--color-text-tertiary)]"
                  : "text-[var(--color-text-primary)]"
              }`}
              title={
                isExternalNode
                  ? `${n.node_id} (external dependency, not part of this repo)`
                  : n.node_id
              }
            >
              {truncatePath(n.node_id, 48)}
            </span>
            <span className="shrink-0 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
              {n.edge_type}
            </span>
          </>
        );
        return (
          <li key={`${n.node_id}-${n.edge_type}`} className="py-2">
            {/* Rule 9: an external node has no page behind it, so it gets no
                hover ground and no href — plain tertiary mono instead. */}
            {isExternalNode ? (
              <div className="flex items-center gap-3">{row}</div>
            ) : (
              <A
                href={href}
                className="-mx-2 flex items-center gap-3 rounded px-2 transition-colors hover:bg-[var(--color-bg-elevated)]"
              >
                {row}
              </A>
            )}
            {n.imported_names.length > 0 && (
              <p
                className="mt-0.5 truncate font-mono text-[10px] text-[var(--color-text-tertiary)]"
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
  );
}

/**
 * The bound, said out loud, and measured against what is actually drawn.
 *
 * The degree counts are a SQL `COUNT` over every edge; the lists beside them
 * are the server's ranked cut, 20 per direction. Printing the count over a
 * capped list with nothing on screen saying so is the silent-cap lie: the
 * reader is told 137 and can count 20. Renders nothing when the list is whole,
 * because then there is no bound to state.
 */
function shown(drawn: number, total: number) {
  if (drawn >= total) return null;
  return (
    <>
      {" "}
      — <Fig>{drawn}</Fig> of them below, the strongest edges first
    </>
  );
}

export function FileGraphTab({
  graph,
  filePath,
  linkPrefix,
  fileHref,
  symbolHref,
  LinkComponent = "a",
}: FileGraphTabProps) {
  const A = LinkComponent;
  if (!graph) {
    return (
      <EmptyState
        titleAs="h2"
        icon={<Network className="h-8 w-8" />}
        title="Not in the dependency graph"
        description="Files land in the graph once their imports are parsed. An excluded path or an unsupported language leaves this empty."
      />
    );
  }

  const pct = Math.round(graph.pagerank_percentile);
  const community = graph.community_label ?? `community #${graph.community_id}`;

  return (
    <div>
      {/* Rule 14: one verb per section header, and the verb for this whole tab
          is "go to the graph". "View symbols" steers a different subject, so it
          sits on the section about what this file pulls in rather than being
          stacked beside the first one. */}
      <FileSection
        first
        title="Depended on by"
        description={
          <>
            <Fig>{graph.in_degree}</Fig> {graph.in_degree === 1 ? "file imports" : "files import"}{" "}
            this one
            {shown(graph.dependents.length, graph.in_degree)}. Its PageRank puts it in the{" "}
            <Fig>{pct}th</Fig> percentile of the repository, inside {community}.
          </>
        }
        action={
          <A
            href={`${linkPrefix}/architecture?view=files&node=${encodeURIComponent(filePath)}`}
            className="text-sm font-medium text-[var(--color-accent-primary)] hover:underline"
          >
            Show in the dependency graph <span aria-hidden>→</span>
          </A>
        }
      >
        <NeighborList
          neighbors={graph.dependents}
          fileHref={fileHref}
          symbolHref={symbolHref}
          LinkComponent={LinkComponent}
        />
      </FileSection>

      <FileSection
        title="Depends on"
        description={
          <>
            <Fig>{graph.out_degree}</Fig> {graph.out_degree === 1 ? "edge" : "edges"} out
            {shown(graph.dependencies.length, graph.out_degree)}. Nodes outside the repository
            are listed but not linked — there is no page behind them.
          </>
        }
        action={
          <A
            href={`${linkPrefix}/architecture?view=symbols&file=${encodeURIComponent(filePath)}`}
            className="text-sm font-medium text-[var(--color-accent-primary)] hover:underline"
          >
            View symbols <span aria-hidden>→</span>
          </A>
        }
      >
        <NeighborList
          neighbors={graph.dependencies}
          fileHref={fileHref}
          symbolHref={symbolHref}
          LinkComponent={LinkComponent}
        />
      </FileSection>
    </div>
  );
}
