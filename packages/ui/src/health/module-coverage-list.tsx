"use client";

import { useMemo, useState } from "react";
import { ChevronRight, Search } from "lucide-react";
import { CoverageBar } from "./coverage-bar";

export interface ModuleCoverageRow {
  module: string;
  files: number;
  covered_lines: number;
  total_lines: number;
  line_coverage_pct: number;
}

export interface ModuleCoverageListProps {
  modules: ModuleCoverageRow[];
  /** Jump to a directory's files (optional; renders module labels as links). */
  onSelectModule?: ((module: string) => void) | undefined;
}

/**
 * Coverage rolled up into a two-level tree: every directory is grouped under
 * its top-level package (`packages/core`, `tests/unit`, …) so the flat 100+
 * row dump becomes navigable. Groups show a weighted rollup bar and expand to
 * their child directories, worst-covered first. A directory with no coverable
 * lines renders "—" rather than a misleading red 0%.
 */
interface ModuleGroup {
  key: string;
  covered: number;
  total: number;
  files: number;
  pct: number | null;
  children: ModuleCoverageRow[];
}

function topKey(module: string): string {
  if (module === "(root)") return module;
  const parts = module.split("/");
  return parts.length > 1 ? parts.slice(0, 2).join("/") : (parts[0] ?? module);
}

function relLabel(module: string, groupKey: string): string {
  if (module === groupKey) return "./";
  return module.startsWith(groupKey + "/") ? module.slice(groupKey.length + 1) : module;
}

export function ModuleCoverageList({ modules, onSelectModule }: ModuleCoverageListProps) {
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState<Set<string>>(new Set());

  const groups = useMemo<ModuleGroup[]>(() => {
    const byKey = new Map<string, ModuleGroup>();
    for (const m of modules) {
      const key = topKey(m.module);
      let g = byKey.get(key);
      if (!g) {
        g = { key, covered: 0, total: 0, files: 0, pct: null, children: [] };
        byKey.set(key, g);
      }
      g.covered += m.covered_lines;
      g.total += m.total_lines;
      g.files += m.files;
      g.children.push(m);
    }
    const out = [...byKey.values()];
    for (const g of out) {
      g.pct = g.total > 0 ? (g.covered / g.total) * 100 : null;
      g.children.sort((a, b) => a.line_coverage_pct - b.line_coverage_pct);
    }
    // Worst-covered groups first; groups with no coverable lines sink to the end.
    out.sort((a, b) => (a.pct ?? 101) - (b.pct ?? 101));
    return out;
  }, [modules]);

  const filtered = useMemo(() => {
    if (!search) return groups;
    const s = search.toLowerCase();
    return groups
      .map((g) => {
        if (g.key.toLowerCase().includes(s)) return g;
        const children = g.children.filter((c) => c.module.toLowerCase().includes(s));
        return children.length ? { ...g, children } : null;
      })
      .filter((g): g is ModuleGroup => g !== null);
  }, [groups, search]);

  // When searching, auto-expand matches so the hits are visible.
  const isOpen = (key: string) => (search ? true : open.has(key));
  const toggle = (key: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  if (modules.length === 0) {
    return (
      <p className="text-sm text-[var(--color-text-tertiary)]">
        No coverage data for any module.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      <div className="relative w-full sm:w-72">
        <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--color-text-tertiary)]" />
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Filter modules…"
          className="w-full rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] py-1.5 pl-7 pr-2 text-xs focus:border-[var(--color-border-strong)] focus:outline-none"
        />
      </div>

      <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] divide-y divide-[var(--color-border-default)]">
        {filtered.map((g) => {
          const expanded = isOpen(g.key);
          return (
            <div key={g.key}>
              <button
                type="button"
                onClick={() => toggle(g.key)}
                className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-[var(--color-bg-elevated)]"
                aria-expanded={expanded}
              >
                <ChevronRight
                  className={`h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)] transition-transform ${expanded ? "rotate-90" : ""}`}
                />
                <span className="font-mono text-sm text-[var(--color-text-primary)] truncate">
                  {g.key}
                </span>
                <span className="ml-auto shrink-0 text-xs text-[var(--color-text-tertiary)] tabular-nums">
                  {g.children.length} dir{g.children.length === 1 ? "" : "s"} ·{" "}
                  {g.total > 0 ? `${g.covered.toLocaleString()}/${g.total.toLocaleString()} lines` : "no coverable lines"}
                </span>
                <div className="w-40 shrink-0">
                  <CoverageBar value={g.pct} size="sm" />
                </div>
              </button>

              {expanded ? (
                <div className="divide-y divide-[var(--color-table-divider)] border-t border-[var(--color-table-divider)] bg-[var(--color-bg-muted)]/30">
                  {g.children.map((c) => {
                    const cPct = c.total_lines > 0 ? c.line_coverage_pct : null;
                    return (
                      <div
                        key={c.module}
                        className={`flex items-center gap-3 py-2 pl-11 pr-4 ${onSelectModule ? "cursor-pointer hover:bg-[var(--color-bg-elevated)]" : ""}`}
                        onClick={onSelectModule ? () => onSelectModule(c.module) : undefined}
                      >
                        <span
                          className="font-mono text-xs text-[var(--color-text-secondary)] truncate"
                          title={c.module}
                        >
                          {relLabel(c.module, g.key)}
                        </span>
                        <span className="ml-auto shrink-0 text-[11px] text-[var(--color-text-tertiary)] tabular-nums">
                          {c.files} file{c.files === 1 ? "" : "s"}
                        </span>
                        <div className="w-40 shrink-0">
                          <CoverageBar value={cPct} size="sm" />
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
