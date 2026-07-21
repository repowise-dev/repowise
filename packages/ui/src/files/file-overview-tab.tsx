import { ArrowRight, Bug, Code2, Network } from "lucide-react";
import type { FileDetailResponse } from "@repowise-dev/types/files";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { Badge } from "../ui/badge";
import { StatGrid, StatTile } from "../shared/stat-grid";
import { scoreTextColor } from "../health/tokens";
import { truncatePath } from "../lib/format";
import { SYMBOL_FIX_TITLE } from "../git/fix-history-badge";

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
  // Keyed on the row's own symbol_id, not a rebuilt `${path}::${name}`: some
  // extractors mint ids from the qualified name, and those would never match.
  const rawFixCounts = data.git?.fix_symbol_counts ?? {};
  const fixedCount = Object.values(rawFixCounts).filter((n) => n > 0).length;
  // Attribution marks every symbol overlapping a fix's hunks, so one commit
  // that fixed a bug and reformatted the file lands on all of them. A map
  // covering most of the file says nothing about *which* symbol is the
  // problem, and painting every chip red is noise, so the markers stand down
  // and the file-level count on the History tab carries it instead.
  const perSymbolSignal =
    fixedCount > 0 && data.symbols.length > 0 && fixedCount * 2 <= data.symbols.length;
  const fixesIn = (symbolId: string) =>
    perSymbolSignal ? (rawFixCounts[symbolId] ?? 0) : 0;

  // Complexity picks the list, as it always has: biasing the whole sort on a
  // "was fixed" boolean would evict the file's most complex symbol in favour
  // of eight trivial helpers that took one fix each.
  const byComplexity = [...data.symbols].sort(
    (a, b) => b.complexity_estimate - a.complexity_estimate,
  );
  const keySymbols = byComplexity.slice(0, 8);
  // One reserved slot, so the symbol that keeps breaking cannot be hidden by
  // seven complicated neighbours. Only reachable when something was cut.
  if (byComplexity.length > keySymbols.length) {
    const worst = [...data.symbols].sort(
      (a, b) =>
        fixesIn(b.symbol_id) - fixesIn(a.symbol_id) ||
        b.complexity_estimate - a.complexity_estimate,
    )[0];
    if (worst && fixesIn(worst.symbol_id) > 0 && !keySymbols.includes(worst)) {
      keySymbols[keySymbols.length - 1] = worst;
    }
  }

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
            {keySymbols.map((s) => {
              const fixes = fixesIn(s.symbol_id);
              return (
                <a
                  key={s.symbol_id}
                  href={symbolHref(s.symbol_id)}
                  className="inline-flex items-center gap-1 rounded border border-[var(--color-border-default)] px-2 py-0.5 font-mono text-xs text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-accent-primary)] hover:text-[var(--color-accent-primary)]"
                >
                  {s.name}
                  <span className="text-[10px] text-[var(--color-text-tertiary)]">{s.kind}</span>
                  {fixes > 0 && (
                    <span
                      className="inline-flex items-center gap-0.5 text-[10px] tabular-nums text-[var(--color-error)]"
                      title={SYMBOL_FIX_TITLE}
                    >
                      <Bug className="h-2.5 w-2.5" />
                      {fixes}
                    </span>
                  )}
                </a>
              );
            })}
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
                const isExternal = n.node_id.startsWith("external:");
                const isSymbol = !isExternal && (n.node_type === "symbol" || n.node_id.includes("::"));
                if (isExternal) {
                  return (
                    <p
                      key={`out-${n.node_id}`}
                      className="block truncate font-mono text-xs text-[var(--color-text-tertiary)]"
                      title={`${n.node_id} (external dependency, not part of this repo)`}
                    >
                      {truncatePath(n.node_id, 30)}
                    </p>
                  );
                }
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
