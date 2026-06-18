import { Flame } from "lucide-react";
import { Badge } from "../ui/badge";
import { fileEntityPath, symbolEntityPath } from "../shared/entity/routes";
import { EntityHeader } from "../shared/entity";
import type { BreadcrumbSegment } from "../shared/breadcrumb";
import type {
  SymbolDetailData,
  SymbolDetailResponse,
} from "@repowise-dev/types/symbols";
import { SymbolDetailBody } from "./symbol-detail-body";

export interface SymbolPageProps {
  data: SymbolDetailResponse;
  repoId: string;
  linkPrefix?: string;
  breadcrumb?: BreadcrumbSegment[];
  LinkComponent?: React.ElementType<{
    href: string;
    className?: string;
    children: React.ReactNode;
  }>;
  onOpenBlastRadius?: () => void;
}

/**
 * Pure mapping of the route's `SymbolDetailResponse` onto the unified
 * `SymbolDetailData`. The drawer surface normalizes its `CodeSymbol` + graph
 * APIs into the same shape (in its web wrapper), so both render one body.
 *
 * The route populates every field its endpoint actually returns — symbol,
 * graph (callers/callees + degrees), function-blame, governing decisions, and
 * file_context. It deliberately leaves file-level git intelligence (git,
 * co_changes, dead_code) and the graph-percentile / entry-point / heritage
 * feeds undefined: `SymbolDetailResponse` does not carry them, so the body's
 * presence-guards hide those blocks rather than the mapping fabricating data.
 * Governing decisions are passed through and rendered inside `SymbolDetailBody`
 * (not the header) so they appear exactly once on both surfaces.
 */
export function normalizeSymbolDetailResponse(
  data: SymbolDetailResponse,
): SymbolDetailData {
  const s = data.symbol;
  return {
    identity: {
      name: s.name,
      qualified_name: s.qualified_name,
      kind: s.kind,
      visibility: s.visibility,
      language: s.language,
      is_async: s.is_async,
      file_path: s.file_path,
      start_line: s.start_line,
      parent_name: s.parent_name,
      file_is_hotspot: s.file_is_hotspot ?? null,
    },
    signature: s.signature,
    docstring: s.docstring,
    importance_score: s.importance_score ?? null,
    complexity_estimate: s.complexity_estimate,
    blame_mod_count: s.blame_mod_count ?? null,
    blame_recent_mod_count: s.blame_recent_mod_count ?? null,
    blame_median_author_time: s.blame_median_author_time ?? null,
    blame_owner_name: s.blame_owner_name ?? null,
    blame_owner_line_pct: s.blame_owner_line_pct ?? null,
    graph: {
      in_degree: data.graph.in_degree,
      out_degree: data.graph.out_degree,
      callers: data.graph.callers.map((c) => ({
        symbol_id: c.symbol_id,
        name: c.name,
        file: c.file,
        edge_type: c.edge_type,
        confidence: c.confidence,
      })),
      callees: data.graph.callees.map((c) => ({
        symbol_id: c.symbol_id,
        name: c.name,
        file: c.file,
        edge_type: c.edge_type,
        confidence: c.confidence,
      })),
    },
    governing_decisions: data.governing_decisions,
    file_context: {
      health_score: data.file_context.health_score,
      language: data.file_context.language,
    },
  };
}

/**
 * The canonical routed symbol page: a standardized entity header (eyebrow,
 * breadcrumb, identity, one-line summary, primary signal) over the shared
 * `SymbolDetailBody`.
 */
export function SymbolPage({
  data,
  repoId,
  linkPrefix,
  breadcrumb,
  LinkComponent,
  onOpenBlastRadius,
}: SymbolPageProps) {
  const prefix = linkPrefix ?? `/repos/${repoId}`;
  const s = data.symbol;
  const normalized = normalizeSymbolDetailResponse(data);

  return (
    <div className="space-y-4">
      <EntityHeader
        eyebrow="SYMBOL"
        breadcrumb={breadcrumb ?? []}
        identity={
          <span className="font-mono break-all" title={s.qualified_name || s.name}>
            {s.name}
          </span>
        }
        summary={symbolSummary(data)}
        metaBadges={
          <>
            <Badge variant="outline" className="h-5 text-[10px]">
              {s.kind}
            </Badge>
            {s.is_async && (
              <Badge variant="outline" className="h-5 text-[10px]">
                async
              </Badge>
            )}
            <Badge variant="outline" className="h-5 text-[10px] capitalize">
              {s.visibility}
            </Badge>
            {s.file_is_hotspot && (
              <Badge
                variant="outline"
                className="h-5 text-[10px] text-[var(--color-error)] border-[var(--color-error)]/30"
              >
                <Flame className="h-2.5 w-2.5" /> hot file
              </Badge>
            )}
            <a
              href={fileEntityPath(prefix, s.file_path)}
              title={`${s.file_path}:${s.start_line}`}
              className="block max-w-full truncate font-mono text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-accent-primary)] hover:underline"
            >
              {s.file_path}:{s.start_line}
            </a>
          </>
        }
        {...(LinkComponent ? { LinkComponent } : {})}
      />

      <SymbolDetailBody
        data={normalized}
        symbolHref={(symId) => symbolEntityPath(prefix, symId)}
        fileHref={(p) => fileEntityPath(prefix, p)}
        {...(onOpenBlastRadius ? { onOpenBlastRadius } : {})}
      />
    </div>
  );
}

/** Synthesised "what is this" one-liner for a symbol — the missing summary. */
function symbolSummary(data: SymbolDetailResponse): string {
  const s = data.symbol;
  if (s.docstring) {
    const firstLine = s.docstring.split("\n").find((l) => l.trim()) ?? "";
    if (firstLine.trim()) return firstLine.trim();
  }
  const kind = s.kind || "symbol";
  const article = /^[aeiou]/i.test(kind) ? "An" : "A";
  const where = s.file_path.split("/").pop() ?? s.file_path;
  return `${article} undocumented ${kind} in ${where}.`;
}
