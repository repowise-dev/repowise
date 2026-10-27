"use client";

/**
 * The canvas's own "nothing to draw" copy, centred over the canvas box: an
 * error, no services yet, or services with no relationships. Null while
 * loading or when there is something to draw.
 */

import { EmptyState } from "../../shared/empty-state";
import { toFriendlyMessage } from "../../lib/errors";

export interface CanvasEmptyProps {
  error?: Error | null | undefined;
  loading: boolean;
  hasGraph: boolean;
  hasEdges: boolean;
}

function message({ error, loading, hasGraph, hasEdges }: CanvasEmptyProps): { title: string; description: string } | null {
  if (error) return { title: "Couldn't load the system map", description: toFriendlyMessage(error) };
  if (loading) return null;
  if (!hasGraph) {
    return {
      title: "No services to map yet",
      description:
        "The system map appears once the workspace has at least two indexed repositories with detected cross-repo relationships.",
    };
  }
  if (!hasEdges) {
    return {
      title: "No cross-repo relationships detected",
      description: "Services are indexed, but no HTTP, gRPC, event, package, database or co-change links were found between them yet.",
    };
  }
  return null;
}

export function SystemMapCanvasEmpty(props: CanvasEmptyProps) {
  const m = message(props);
  if (!m) return null;
  return (
    <div className="pointer-events-none absolute inset-0 z-[3] flex items-center justify-center">
      <EmptyState title={m.title} description={m.description} />
    </div>
  );
}
