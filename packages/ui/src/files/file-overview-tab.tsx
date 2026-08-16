import { FileQuestion } from "lucide-react";
import type { FileDetailResponse } from "@repowise-dev/types/files";
import { isExternal, nodeKind } from "@repowise-dev/types";
import { EmptyState } from "../shared/empty-state";
import { formatLOC, truncatePath } from "../lib/format";
import { SYMBOL_FIX_TITLE } from "../git/fix-history-badge";
import { FileSection, Fig } from "./file-section";

interface FileOverviewTabProps {
  data: FileDetailResponse;
  /** Build a symbol-page href. */
  symbolHref: (symbolId: string) => string;
  /** Build a file-page href. */
  fileHref: (path: string) => string;
}

/**
 * The default file tab: the symbols worth knowing about, an inline dependency
 * snapshot, and anything the dead-code pass could not reach — so an
 * undocumented file is never empty (the old default landed on an empty
 * "No documentation yet" Doc tab).
 *
 * The headline figures are **not** here. They moved into the header's
 * `StatRibbon`, which is on screen whichever tab is open; a four-tile grid
 * repeating them under the tab row was the same five numbers twice.
 */
export function FileOverviewTab({ data, symbolHref, fileHref }: FileOverviewTabProps) {
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

  // Two full sorts of every symbol in the file to pick eight chips. Left
  // unmemoized deliberately: `useMemo` would make this a client component, and
  // this is one of the six tab bodies that are pure and hookless — which is
  // what lets the hydration boundary sit at `file-health-tab` alone. Only the
  // active panel is rendered, and it is rendered on the server, so the sorts
  // run once per request rather than on every parent render.
  //
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

  const hasNeighbors =
    !!data.graph && (data.graph.dependents.length > 0 || data.graph.dependencies.length > 0);

  if (keySymbols.length === 0 && !hasNeighbors && data.dead_code.length === 0) {
    return (
      <EmptyState
        titleAs="h2"
        icon={<FileQuestion className="h-8 w-8" />}
        title="Nothing extracted from this file yet"
        description="Symbols and dependency edges land with the next index. A file with none is usually data, config or a language Repowise does not parse."
      />
    );
  }

  return (
    <div>
      {keySymbols.length > 0 && (
        <FileSection
          first
          title="Key symbols"
          description={
            <>
              {keySymbols.length < data.symbols.length ? (
                <>
                  The <Fig>{keySymbols.length}</Fig> most complex of{" "}
                  <Fig>{data.symbols.length}</Fig> extracted symbols
                </>
              ) : (
                <>
                  All <Fig>{data.symbols.length}</Fig> extracted{" "}
                  {data.symbols.length === 1 ? "symbol" : "symbols"}, ordered by complexity
                </>
              )}
              {perSymbolSignal
                ? ", each carrying the number of prior bug fixes that touched it."
                : "."}
            </>
          }
        >
          <div className="flex flex-wrap gap-1.5">
            {keySymbols.map((s) => {
              const fixes = fixesIn(s.symbol_id);
              return (
                <a
                  key={s.symbol_id}
                  href={symbolHref(s.symbol_id)}
                  className="inline-flex items-center gap-1.5 rounded border border-[var(--color-border-default)] px-2 py-1 font-mono text-xs text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-accent-primary)] hover:text-[var(--color-accent-primary)]"
                >
                  {s.name}
                  <span className="text-[10px] text-[var(--color-text-tertiary)]">{s.kind}</span>
                  {fixes > 0 && (
                    <span
                      className="tabular-nums text-[10px] text-[var(--color-error)]"
                      title={SYMBOL_FIX_TITLE}
                    >
                      {fixes} {fixes === 1 ? "fix" : "fixes"}
                    </span>
                  )}
                </a>
              );
            })}
          </div>
        </FileSection>
      )}

      {data.graph && hasNeighbors && (
        <FileSection
          first={keySymbols.length === 0}
          title="Nearest neighbours"
          description={
            <>
              <Fig>{data.graph.in_degree}</Fig>{" "}
              {data.graph.in_degree === 1 ? "file imports" : "files import"} this one and it
              imports <Fig>{data.graph.out_degree}</Fig>. Five of each are below, and the
              Dependencies tab widens that to twenty — the aggregate ranks and cuts the edge
              list server-side, so neither view is the whole graph. The dependency canvas is.
            </>
          }
        >
          <div className="grid grid-cols-1 gap-x-10 gap-y-6 sm:grid-cols-2">
            <NeighborColumn
              label="Depended on by"
              nodes={data.graph.dependents.slice(0, 5).map((n) => n.node_id)}
              fileHref={fileHref}
              symbolHref={symbolHref}
            />
            <NeighborColumn
              label="Depends on"
              nodes={data.graph.dependencies.slice(0, 5).map((n) => n.node_id)}
              fileHref={fileHref}
              symbolHref={symbolHref}
            />
          </div>
        </FileSection>
      )}

      {data.dead_code.length > 0 && (
        <FileSection
          first={keySymbols.length === 0 && !hasNeighbors}
          title="Unreachable"
          description={
            <>
              <Fig>{data.dead_code.length}</Fig>{" "}
              {data.dead_code.length === 1 ? "symbol" : "symbols"} totalling{" "}
              <Fig>{formatLOC(deadLines)}</Fig> lines that the dead-code pass found no
              reachable caller for. Confidence is per finding; check the reason before
              deleting.
            </>
          }
        >
          <ul className="divide-y divide-[var(--color-border-default)] border-y border-[var(--color-border-default)]">
            {data.dead_code.map((f) => (
              <li key={f.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-2.5">
                <span className="font-mono text-xs text-[var(--color-text-primary)]">
                  {f.symbol_name ?? f.kind}
                </span>
                <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                  {f.kind}
                </span>
                {/* Drops to its own line below `sm`. Sharing a 390px row with
                    the name, the kind and the confidence squeezed the reason
                    into a four-word column. */}
                <span className="order-last min-w-0 basis-full text-xs text-[var(--color-text-secondary)] sm:order-none sm:basis-0 sm:flex-1">
                  {f.reason}
                </span>
                <span className="shrink-0 font-mono text-[10px] tabular-nums text-[var(--color-text-tertiary)]">
                  {Math.round(f.confidence * 100)}% · {f.lines} ln
                </span>
              </li>
            ))}
          </ul>
        </FileSection>
      )}
    </div>
  );
}

/** One side of the neighbour snapshot. External nodes render as plain mono with
 *  no ground and no href — rule 9, following them goes nowhere. */
function NeighborColumn({
  label,
  nodes,
  fileHref,
  symbolHref,
}: {
  label: string;
  nodes: string[];
  fileHref: (path: string) => string;
  symbolHref: (id: string) => string;
}) {
  return (
    <div className="min-w-0">
      <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        {label}
      </p>
      <ul className="mt-2 space-y-1.5">
        {nodes.length === 0 && (
          <li className="text-xs text-[var(--color-text-tertiary)]">None in the indexed graph.</li>
        )}
        {nodes.map((id) => {
          const external = isExternal(id);
          const symbol = !external && nodeKind(id) === "symbol";
          if (external) {
            return (
              <li
                key={id}
                className="truncate font-mono text-xs text-[var(--color-text-tertiary)]"
                title={`${id} (external dependency, not part of this repo)`}
              >
                {truncatePath(id, 40)}
              </li>
            );
          }
          return (
            <li key={id} className="min-w-0">
              <a
                href={symbol ? symbolHref(id) : fileHref(id)}
                className="block truncate font-mono text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-accent-primary)]"
                title={id}
              >
                {truncatePath(id, 40)}
              </a>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
