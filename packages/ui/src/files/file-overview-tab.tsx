import { ArrowRight, Code2, Network } from "lucide-react";
import type { FileDetailResponse } from "@repowise-dev/types/files";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { Badge } from "../ui/badge";
import { StatGrid, StatTile } from "../shared/stat-grid";
import { scoreTextColor } from "../health/tokens";
import { truncatePath } from "../lib/format";

interface FileOverviewTabProps {
  data: FileDetailResponse;
  /** Build a symbol-page href. */
  symbolHref: (symbolId: string) => string;
  /** Build a file-page href. */
  fileHref: (path: string) => string;
}

/**
 * The default file tab: a compact health glance, key symbols, and an inline
 * dependency snapshot so undocumented files are never empty (the old default
 * landed on an empty "No documentation yet" Doc tab).
 */
export function FileOverviewTab({ data, symbolHref, fileHref }: FileOverviewTabProps) {
  const score = data.health.metric?.score;
  const deadLines = data.dead_code.reduce((s, f) => s + f.lines, 0);
  const keySymbols = [...data.symbols]
    .sort((a, b) => b.complexity_estimate - a.complexity_estimate)
    .slice(0, 8);

  return (
    <div className="space-y-4">
      {/* ── Health glance ── */}
      <StatGrid columns={4}>
        <StatTile
          label="Health"
          value={
            <span className={score != null ? scoreTextColor(score) : undefined}>
              {score != null ? `${score.toFixed(1)}/10` : "—"}
            </span>
          }
        />
        <StatTile
          label="Symbols"
          value={data.graph?.symbol_count ?? data.symbols.length}
        />
        <StatTile
          label="Dependencies"
          value={data.graph ? `${data.graph.in_degree} in · ${data.graph.out_degree} out` : "—"}
        />
        <StatTile
          label="Dead code"
          value={data.dead_code.length > 0 ? `${data.dead_code.length} (${deadLines} ln)` : "0"}
        />
      </StatGrid>

      {/* ── Key symbols ── */}
      {keySymbols.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-1.5 text-sm font-medium">
              <Code2 className="h-4 w-4 text-[var(--color-text-secondary)]" /> Key symbols
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-1.5">
            {keySymbols.map((s) => (
              <a
                key={s.symbol_id}
                href={symbolHref(s.symbol_id)}
                className="inline-flex items-center gap-1 rounded border border-[var(--color-border-default)] px-2 py-0.5 font-mono text-xs text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-accent-primary)] hover:text-[var(--color-accent-primary)]"
              >
                {s.name}
                <span className="text-[10px] text-[var(--color-text-tertiary)]">{s.kind}</span>
              </a>
            ))}
          </CardContent>
        </Card>
      )}

      {/* ── Inline dependency snapshot ── */}
      {data.graph && (data.graph.dependents.length > 0 || data.graph.dependencies.length > 0) && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-1.5 text-sm font-medium">
              <Network className="h-4 w-4 text-[var(--color-text-secondary)]" /> Dependency snapshot
            </CardTitle>
          </CardHeader>
          <CardContent className="grid grid-cols-[1fr_auto_1fr] items-start gap-3">
            <div className="space-y-1">
              <p className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
                Dependents ({data.graph.in_degree})
              </p>
              {data.graph.dependents.slice(0, 5).map((n) => (
                <a
                  key={`in-${n.node_id}`}
                  href={fileHref(n.node_id)}
                  className="block truncate font-mono text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-accent-primary)]"
                  title={n.node_id}
                >
                  {truncatePath(n.node_id, 30)}
                </a>
              ))}
            </div>
            <ArrowRight className="mt-5 h-4 w-4 text-[var(--color-text-tertiary)]" />
            <div className="space-y-1">
              <p className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
                Dependencies ({data.graph.out_degree})
              </p>
              {data.graph.dependencies.slice(0, 5).map((n) => {
                const isSymbol = n.node_type === "symbol" || n.node_id.includes("::");
                return (
                  <a
                    key={`out-${n.node_id}`}
                    href={isSymbol ? symbolHref(n.node_id) : fileHref(n.node_id)}
                    className="block truncate font-mono text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-accent-primary)]"
                    title={n.node_id}
                  >
                    {truncatePath(n.node_id, 30)}
                  </a>
                );
              })}
            </div>
          </CardContent>
        </Card>
      )}

      {data.graph?.is_entry_point && (
        <Badge variant="outline" className="text-[10px] text-[var(--color-info)] border-[var(--color-info)]/30">
          Entry point
        </Badge>
      )}
    </div>
  );
}
