"use client";

import { useMemo, useState, type ReactNode } from "react";
import { Search } from "lucide-react";
import type { FileLanguageCount, FileRow } from "@repowise-dev/types/files";
import { bandForScore } from "@repowise-dev/types/health";
import { cn } from "../lib/cn";
import { formatLOC, formatNumber } from "../lib/format";
import { FilesTreemap, type TreemapColor, type TreemapSize } from "./files-treemap";
import { FilesTable, type SortKey } from "./files-table";

interface FilesIndexProps {
  files: FileRow[];
  languages: FileLanguageCount[];
  /** Build the per-file page href (e.g. `/repos/:id/files/:path`). */
  fileHref: (path: string) => string;
}

type TestFilter = "all" | "code" | "tests";

/** How the active sort reads in the section's sentence. Named so the copy can
 *  never claim an order the table is not in. */
const SORT_LABEL: Record<SortKey, string> = {
  dependents: "dependents",
  health: "health",
  churn: "churn",
  loc: "lines",
  coverage: "coverage",
  name: "path",
};

const SORT_DEFAULT_DIR: Record<SortKey, "asc" | "desc"> = {
  dependents: "desc",
  health: "asc", // lowest (worst) health first is the interesting end
  churn: "desc",
  loc: "desc",
  coverage: "asc",
  name: "asc",
};

/**
 * A segmented control, optionally named.
 *
 * The `label` is not decoration on the map's two controls. They steer different
 * axes and sat unlabelled side by side, so `Importance | Size` next to
 * `Health | Language` gave the reader four pills and no statement of which pair
 * did what. One axis, one control, and the control says which axis. The
 * toolbar's `All | Code | Tests` needs no such help and passes nothing.
 */
function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label?: string;
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  const control = (
    <div className="inline-flex rounded-md border border-[var(--color-border-default)] p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "rounded px-2 py-1 text-xs font-medium transition-colors",
            value === o.value
              ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
              : "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
  if (!label) return control;
  return (
    <div className="inline-flex items-center gap-2">
      <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        {label}
      </span>
      {control}
    </div>
  );
}

/** A figure inside the lede sentence. No `tabular-nums`: it is not in a column
 *  and cannot change in place, which is where that rule applies. */
function Fig({ children }: { children: ReactNode }) {
  return <span className="font-medium text-[var(--color-text-primary)]">{children}</span>;
}

export function FilesIndex({ files, languages, fileHref }: FilesIndexProps) {
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("dependents");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [langFilter, setLangFilter] = useState<string>("");
  const [testFilter, setTestFilter] = useState<TestFilter>("all");
  const [sizeBy, setSizeBy] = useState<TreemapSize>("dependents");
  const [colorBy, setColorBy] = useState<TreemapColor>("health");
  const [prefix, setPrefix] = useState<string[]>([]);

  // The figures the lede sentence spends — one pass over the full set.
  const kpis = useMemo(() => {
    let loc = 0;
    let scored = 0;
    let healthy = 0;
    for (const f of files) {
      loc += f.loc ?? 0;
      if (f.defect_score != null) {
        scored++;
        // `bandForScore`, not a local `>= 7`. The threshold here was 7 while
        // every band this page paints starts Healthy at 8, so the strip was
        // reporting a share of files as healthy that the map beside it was
        // colouring amber.
        if (bandForScore(f.defect_score) === "healthy") healthy++;
      }
    }
    return {
      total: files.length,
      loc,
      langCount: languages.length,
      scored,
      healthyPct: scored > 0 ? Math.round((healthy / scored) * 100) : null,
    };
  }, [files, languages]);

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir(SORT_DEFAULT_DIR[key]);
    }
  };

  // Filter (prefix scope → test → language → fuzzy) then sort. Memoized so
  // keystrokes only recompute when an input actually changes.
  const tableFiles = useMemo(() => {
    const pfx = prefix.length ? prefix.join("/") + "/" : "";
    const q = query.trim().toLowerCase();
    const filtered = files.filter((f) => {
      if (pfx && !f.file_path.startsWith(pfx)) return false;
      if (testFilter === "tests" && !f.is_test) return false;
      if (testFilter === "code" && f.is_test) return false;
      if (langFilter && f.language !== langFilter) return false;
      if (q && !f.file_path.toLowerCase().includes(q)) return false;
      return true;
    });

    const dir = sortDir === "asc" ? 1 : -1;
    const num = (v: number | null) => (v == null ? -1 : v);
    filtered.sort((a, b) => {
      let cmp = 0;
      switch (sortKey) {
        case "dependents":
          cmp = a.pagerank_pct - b.pagerank_pct;
          break;
        case "health":
          cmp = num(a.defect_score) - num(b.defect_score);
          break;
        case "churn":
          cmp = num(a.churn_pct) - num(b.churn_pct);
          break;
        case "loc":
          cmp = num(a.loc) - num(b.loc);
          break;
        case "coverage":
          cmp = num(a.coverage_pct) - num(b.coverage_pct);
          break;
        case "name":
          cmp = a.file_path.localeCompare(b.file_path);
          break;
      }
      if (cmp === 0) cmp = a.file_path.localeCompare(b.file_path);
      return cmp * dir;
    });
    return filtered;
  }, [files, prefix, query, testFilter, langFilter, sortKey, sortDir]);

  return (
    <div>
      {/*
        The map leads, and nothing figure-shaped sits above it.
        `PageLede` is the house opening for a surface whose subject is a number
        — code health, coverage, dead code — and it is the wrong shape here: a
        52px figure at the top of a page whose subject is a canvas pushes the
        canvas toward the fold to answer a question nobody arrived with. The
        Knowledge Graph and Architecture ports settled this: where the canvas is
        the page, it gets a header row, the base plane and a key row, and the
        figures ride along in the sentence rather than in four boxes above it.
      */}
      <section className="space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
          <div className="min-w-0">
            <h2 className="text-lg font-semibold text-[var(--color-text-primary)]">
              Repository map
            </h2>
            <p className="mt-1 max-w-prose text-sm text-[var(--color-text-secondary)]">
              <Fig>{formatNumber(kpis.total)}</Fig> files across <Fig>{formatLOC(kpis.loc)}</Fig>{" "}
              lines in <Fig>{kpis.langCount}</Fig> {kpis.langCount === 1 ? "language" : "languages"}
              .{" "}
              {kpis.healthyPct != null ? (
                <>
                  <Fig>{kpis.healthyPct}%</Fig> of the <Fig>{formatNumber(kpis.scored)}</Fig>{" "}
                  carrying a health score are healthy.
                </>
              ) : (
                "None carry a health score yet."
              )}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
            <Segmented<TreemapSize>
              label="Area"
              value={sizeBy}
              onChange={setSizeBy}
              options={[
                { value: "dependents", label: "Dependents" },
                { value: "loc", label: "Lines" },
              ]}
            />
            <Segmented<TreemapColor>
              label="Colour"
              value={colorBy}
              onChange={setColorBy}
              options={[
                { value: "health", label: "Health" },
                { value: "language", label: "Language" },
              ]}
            />
          </div>
        </div>
        {/* No card. The thing you came for sits on the base plane, and chrome
            goes around a canvas rather than on it — the header above and the
            key row `FilesTreemap` renders underneath are that chrome. */}
        <FilesTreemap
          files={files}
          fileHref={fileHref}
          sizeBy={sizeBy}
          colorBy={colorBy}
          prefix={prefix}
          onPrefixChange={setPrefix}
        />
      </section>

      {/* The table is a section, not a loose stack under the map: a hairline
          and vertical rhythm are the grouping device, not a border box. */}
      <section className="mt-12 space-y-3 border-t border-[var(--color-border-default)] pt-8">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-[var(--color-text-primary)]">Every file</h2>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
            <Fig>{formatNumber(tableFiles.length)}</Fig>{" "}
            {tableFiles.length === 1 ? "file" : "files"}
            {prefix.length > 0 && (
              <>
                {" "}
                in <Fig>{prefix.join("/")}/</Fig>
              </>
            )}
            , sorted by {SORT_LABEL[sortKey]}. Any column header re-sorts.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-0 flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--color-text-tertiary)]" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter files by path…"
              className="h-9 w-full rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] pl-8 pr-3 text-sm text-[var(--color-text-primary)] outline-none placeholder:text-[var(--color-text-tertiary)] focus:border-[var(--color-accent-primary)]"
            />
          </div>
          <Segmented<TestFilter>
            value={testFilter}
            onChange={setTestFilter}
            options={[
              { value: "all", label: "All" },
              { value: "code", label: "Code" },
              { value: "tests", label: "Tests" },
            ]}
          />
          {languages.length > 1 && (
            <select
              value={langFilter}
              onChange={(e) => setLangFilter(e.target.value)}
              className="h-9 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2 text-sm text-[var(--color-text-secondary)] outline-none focus:border-[var(--color-accent-primary)]"
            >
              <option value="">All languages</option>
              {languages.map((l) => (
                <option key={l.language} value={l.language}>
                  {l.language} ({l.count})
                </option>
              ))}
            </select>
          )}
        </div>

        <FilesTable
          files={tableFiles}
          fileHref={fileHref}
          sortKey={sortKey}
          sortDir={sortDir}
          onSort={handleSort}
        />
      </section>
    </div>
  );
}
