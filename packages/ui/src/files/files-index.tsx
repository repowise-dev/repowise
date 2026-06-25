"use client";

import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import type { FileLanguageCount, FileRow } from "@repowise-dev/types/files";
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

const SORT_DEFAULT_DIR: Record<SortKey, "asc" | "desc"> = {
  importance: "desc",
  health: "asc", // lowest (worst) health first is the interesting end
  churn: "desc",
  loc: "desc",
  coverage: "asc",
  name: "asc",
};

function Segmented<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
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
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-2">
      <p className="text-lg font-semibold tabular-nums text-[var(--color-text-primary)]">{value}</p>
      <p className="text-[11px] uppercase tracking-wide text-[var(--color-text-tertiary)]">
        {label}
      </p>
    </div>
  );
}

export function FilesIndex({ files, languages, fileHref }: FilesIndexProps) {
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("importance");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [langFilter, setLangFilter] = useState<string>("");
  const [testFilter, setTestFilter] = useState<TestFilter>("all");
  const [sizeBy, setSizeBy] = useState<TreemapSize>("importance");
  const [colorBy, setColorBy] = useState<TreemapColor>("health");
  const [prefix, setPrefix] = useState<string[]>([]);

  // KPI strip — cheap aggregate over the full set (memoized).
  const kpis = useMemo(() => {
    let loc = 0;
    let scored = 0;
    let healthy = 0;
    for (const f of files) {
      loc += f.loc ?? 0;
      if (f.defect_score != null) {
        scored++;
        if (f.defect_score >= 7) healthy++;
      }
    }
    return {
      total: files.length,
      loc,
      langCount: languages.length,
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
        case "importance":
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
    <div className="space-y-4">
      {/* KPI strip */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Kpi label="Files" value={formatNumber(kpis.total)} />
        <Kpi label="Lines" value={formatLOC(kpis.loc)} />
        <Kpi label="Languages" value={String(kpis.langCount)} />
        <Kpi label="Healthy" value={kpis.healthyPct != null ? `${kpis.healthyPct}%` : "—"} />
      </div>

      {/* Treemap hero */}
      <div className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3 sm:p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">
            Repository map
          </h2>
          <div className="flex flex-wrap items-center gap-2">
            <Segmented<TreemapSize>
              value={sizeBy}
              onChange={setSizeBy}
              options={[
                { value: "importance", label: "Importance" },
                { value: "loc", label: "Size" },
              ]}
            />
            <Segmented<TreemapColor>
              value={colorBy}
              onChange={setColorBy}
              options={[
                { value: "health", label: "Health" },
                { value: "language", label: "Language" },
              ]}
            />
          </div>
        </div>
        <FilesTreemap
          files={files}
          fileHref={fileHref}
          sizeBy={sizeBy}
          colorBy={colorBy}
          prefix={prefix}
          onPrefixChange={setPrefix}
        />
      </div>

      {/* Toolbar */}
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

      <p className="text-xs text-[var(--color-text-tertiary)]">
        {formatNumber(tableFiles.length)}
        {tableFiles.length === 1 ? " file" : " files"}
        {prefix.length > 0 && ` in ${prefix.join("/")}/`}
      </p>

      <FilesTable
        files={tableFiles}
        fileHref={fileHref}
        sortKey={sortKey}
        sortDir={sortDir}
        onSort={handleSort}
      />
    </div>
  );
}
