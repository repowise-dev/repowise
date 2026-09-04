"use client";

/**
 * Coverage view, on the section design language.
 *
 * The shape is: a lede that leads with the coverage percentage and says in
 * prose what it is built from, then the health × coverage map, then the files
 * where a gap costs something, the module rollup and the per-file table, each
 * grouped by a hairline rather than boxed. Rows link to the file page's
 * coverage tab (the line-level heatmap).
 *
 * What it replaces: five `MetricCard`s in a grid above a bordered chart, a
 * tinted warning panel, a bordered module list and a collapsed table section.
 * Six containers at near-identical weight behind five uppercase labels, which
 * is the box-soup failure the section style exists to fix.
 *
 * Presentation + orchestration only: the host injects data fetching, links,
 * and navigation through a {@link CodeHealthAdapter}.
 */

import { useMemo, useState } from "react";
import { ArrowUpRight, Sparkles } from "lucide-react";
import useSWR from "swr";
import type {
  CoverageFileRow,
  HealthCoverageResponse,
  HealthFinding,
  InferredTestMap,
  ReachedFileRow,
} from "@repowise-dev/types/health";

import { Skeleton } from "../ui/skeleton";
import { EmptyState } from "../shared/empty-state";
import { ResponsiveTable, type ResponsiveColumn } from "../shared/responsive-table";
import { ResultsFooter } from "../shared/results-footer";
import { OverviewSection } from "../overview/section";

import { AiPromptModal } from "./ai-prompt-modal";
import { CoverageLede } from "./coverage-lede";
import { CoverageBar } from "./coverage-bar";
import { ModuleCoverageList } from "./module-coverage-list";
import {
  UntestedHotspotWarning,
  type UntestedHotspotEntry,
} from "./untested-hotspot-warning";
import { RiskCoverageScatter } from "./risk-coverage-scatter";
import { InferredTestsView } from "./inferred-tests-view";
import {
  buildCoverageAiPrompt,
  type CoverageFilePromptInput,
} from "./ai-prompt-builder";
import { scoreBadgeClass } from "./tokens";
import type { CodeHealthAdapter } from "./code-health-adapter";

export function CoverageView({ adapter }: { adapter: CodeHealthAdapter }) {
  const [promptRow, setPromptRow] = useState<CoverageFilePromptInput | null>(null);

  const { data, isLoading, error } = useSWR<HealthCoverageResponse>(
    `code-health-coverage:${adapter.cacheKey}`,
    () => adapter.getCoverage({ limit: 5000 }),
    { revalidateOnFocus: false },
  );

  // The untested_hotspot findings carry dependents_count / commit_count_90d
  // in their details — join them into the warning entries.
  const { data: untestedFindings } = useSWR<HealthFinding[]>(
    `code-health-untested-findings:${adapter.cacheKey}`,
    () =>
      adapter
        .listFindings({ biomarker_type: "untested_hotspot", limit: 50 })
        .catch(() => []),
    { revalidateOnFocus: false },
  );

  const openFilePage = (path: string) =>
    adapter.navigate(`${adapter.fileHref(path)}?tab=coverage`);

  return (
    <div className="flex flex-col gap-6 sm:gap-8">
      {isLoading ? (
        <CoverageSkeleton />
      ) : error ? (
        <EmptyState
          title="Couldn't load coverage data"
          description="The coverage endpoint returned an error. Try refreshing, or re-run the health pass."
        />
      ) : data?.basis === "inferred" && data.inferred ? (
        // No report was ever ingested, and the graph can answer anyway. This
        // branch never renders a measured field: the two bases are separate
        // objects in the payload precisely so one cannot leak into the other's
        // code path.
        <InferredTestsView
          data={data}
          untestedFindings={untestedFindings ?? []}
          onOpenFile={openFilePage}
        />
      ) : !data || data.summary.file_count === 0 ? (
        <NoCoverageState />
      ) : (
        // A report was ingested. `data.inferred` may also be present: when the
        // report never mentioned some files, the graph answers for exactly
        // those, and a separate section renders it. The measured body and the
        // inferred gap stay apart — no percentage is ever derived from the
        // inferred map.
        <CoverageBody
          data={data}
          untestedFindings={untestedFindings ?? []}
          onOpenFile={openFilePage}
          onGeneratePrompt={(r) => setPromptRow(r)}
        />
      )}

      <AiPromptModal
        open={promptRow !== null}
        onOpenChange={(open) => {
          if (!open) setPromptRow(null);
        }}
        filePath={promptRow?.file_path ?? null}
        title="AI test prompt"
        description="A ready-to-paste prompt that asks your AI coding agent to add tests for this file's uncovered lines and branches."
        getPrompt={
          promptRow
            ? (flavor) => buildCoverageAiPrompt({ row: promptRow, flavor })
            : null
        }
      />
    </div>
  );
}

function CoverageBody({
  data,
  untestedFindings,
  onOpenFile,
  onGeneratePrompt,
}: {
  data: HealthCoverageResponse;
  untestedFindings: HealthFinding[];
  onOpenFile: (path: string) => void;
  onGeneratePrompt: (row: CoverageFilePromptInput) => void;
}) {
  const { summary, files, modules } = data;
  // The true count, not the length of what came back. They differ only if the
  // rollup was capped, but the lede claims to describe the repo.
  const moduleCount = data.modules_total ?? modules.length;
  const [search, setSearch] = useState("");
  const [sortField, setSortField] = useState<string>("line_coverage_pct");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("asc");
  const PAGE = 50;
  const [visible, setVisible] = useState(PAGE);

  // Branch coverage is absent for common lcov flows (pytest emits none); only
  // surface the card + column when at least one file actually reports it.
  const hasBranch = summary.branch_coverage_pct != null ||
    files.some((f) => f.branch_coverage_pct != null);

  // A file with zero coverable lines (empty __init__, re-export shim) has no
  // meaningful percentage — treat it as null so it renders "—" and sinks in
  // worst-first sorts instead of masquerading as 0% covered.
  const covOf = (f: CoverageFileRow): number | null =>
    f.total_coverable_lines > 0 ? f.line_coverage_pct : null;

  const num = (v: unknown): number | null =>
    typeof v === "number" && Number.isFinite(v) ? v : null;

  // Untested hotspots: prefer the real biomarker findings (which carry
  // dependents/commit context); fall back to the low-coverage × low-score
  // heuristic when the biomarker never fired (e.g. coverage just ingested).
  const untested = useMemo<UntestedHotspotEntry[]>(() => {
    const covByPath = new Map(files.map((f) => [f.file_path, f]));
    if (untestedFindings.length > 0) {
      return untestedFindings.slice(0, 10).map((f) => {
        const cov = covByPath.get(f.file_path);
        const d = f.details ?? {};
        const entry: UntestedHotspotEntry = {
          file_path: f.file_path,
          line_coverage_pct:
            num(d.line_coverage_pct) ?? cov?.line_coverage_pct ?? null,
          commit_count_90d: num(d.commit_count_90d),
        };
        const dependents = num(d.dependents_count);
        if (dependents != null) entry.dependents_count = dependents;
        if (cov?.health_score != null) entry.health_score = cov.health_score;
        return entry;
      });
    }
    return files
      .filter(
        (f) =>
          f.total_coverable_lines > 0 &&
          f.line_coverage_pct != null &&
          f.line_coverage_pct < 30 &&
          (f.health_score == null || f.health_score < 6),
      )
      .slice(0, 10)
      .map((f) => {
        const entry: UntestedHotspotEntry = {
          file_path: f.file_path,
          line_coverage_pct: f.line_coverage_pct,
        };
        if (f.health_score != null) entry.health_score = f.health_score;
        return entry;
      });
  }, [files, untestedFindings]);

  const scatterPoints = useMemo(
    () =>
      files
        // Same rule as `covOf`: a file with no coverable lines has no coverage
        // percentage. It used to plot at 0% and pile up empty `__init__` files
        // against the left edge, exactly where "critical untested" is written.
        .filter(
          (f) =>
            f.health_score != null &&
            f.line_coverage_pct != null &&
            f.total_coverable_lines > 0,
        )
        .map((f) => ({
          file_path: f.file_path,
          health_score: f.health_score!,
          line_coverage_pct: f.line_coverage_pct,
          nloc: f.nloc ?? 0,
        })),
    [files],
  );

  const filteredFiles = useMemo(() => {
    if (!search) return files;
    const s = search.toLowerCase();
    return files.filter((f) => f.file_path.toLowerCase().includes(s));
  }, [files, search]);

  const sortedFiles = useMemo(() => {
    const dir = sortOrder === "asc" ? 1 : -1;
    const val = (f: CoverageFileRow): number | string | null => {
      switch (sortField) {
        case "file_path":
          return f.file_path;
        case "branch_coverage_pct":
          return f.branch_coverage_pct;
        case "total_coverable_lines":
          return f.total_coverable_lines;
        case "health_score":
          return f.health_score ?? null;
        default:
          return covOf(f);
      }
    };
    return [...filteredFiles].sort((a, b) => {
      const av = val(a);
      const bv = val(b);
      // Nulls (no data / no coverable lines) always sink, regardless of order.
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "string" && typeof bv === "string") {
        return dir * av.localeCompare(bv);
      }
      return dir * ((av as number) - (bv as number));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filteredFiles, sortField, sortOrder]);

  const visibleFiles = sortedFiles.slice(0, visible);

  const onSort = (key: string) => {
    if (key === sortField) {
      setSortOrder((o) => (o === "asc" ? "desc" : "asc"));
    } else {
      setSortField(key);
      // Coverage/health default to worst-first (asc); everything else desc.
      setSortOrder(
        key === "line_coverage_pct" || key === "health_score" ? "asc" : "desc",
      );
    }
    setVisible(PAGE);
  };

  const columns: (ResponsiveColumn<CoverageFileRow> | null)[] = [
    {
      key: "file_path",
      header: "File",
      priority: 1,
      sortable: true,
      render: (f) => (
        <span className="inline-flex items-center gap-1.5 font-mono text-xs text-[var(--color-text-primary)]">
          <span className="truncate max-w-[420px]" title={f.file_path}>
            {f.file_path}
          </span>
          <ArrowUpRight className="h-3 w-3 shrink-0 text-[var(--color-text-tertiary)]" />
        </span>
      ),
    },
    {
      key: "line_coverage_pct",
      header: "Line coverage",
      priority: 1,
      sortable: true,
      render: (f) => <CoverageBar value={covOf(f)} size="sm" />,
    },
    hasBranch
      ? {
          key: "branch_coverage_pct",
          header: "Branch",
          priority: 3,
          align: "right",
          sortable: true,
          render: (f) => (
            <span className="tabular-nums text-[var(--color-text-secondary)]">
              {f.branch_coverage_pct == null
                ? "—"
                : `${f.branch_coverage_pct.toFixed(0)}%`}
            </span>
          ),
        }
      : null,
    {
      key: "total_coverable_lines",
      header: "Lines",
      priority: 3,
      align: "right",
      sortable: true,
      render: (f) => (
        <span className="tabular-nums text-[var(--color-text-tertiary)]">
          {f.total_coverable_lines === 0 ? "—" : f.total_coverable_lines}
        </span>
      ),
    },
    {
      key: "health_score",
      header: "Health",
      priority: 2,
      align: "right",
      sortable: true,
      render: (f) =>
        f.health_score == null ? (
          <span className="text-[var(--color-text-tertiary)]">—</span>
        ) : (
          <span
            className={`inline-block rounded px-1.5 py-0.5 text-xs font-semibold ${scoreBadgeClass(f.health_score)}`}
          >
            {f.health_score.toFixed(1)}
          </span>
        ),
    },
    {
      key: "actions",
      header: "",
      priority: 1,
      align: "right",
      render: (f) => (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onGeneratePrompt({
              file_path: f.file_path,
              line_coverage_pct: f.line_coverage_pct,
              branch_coverage_pct: f.branch_coverage_pct,
              total_coverable_lines: f.total_coverable_lines,
              ...(f.covered_lines && { covered_lines: f.covered_lines }),
              source_format: f.source_format,
              health_score: f.health_score ?? null,
              nloc: f.nloc ?? null,
            });
          }}
          title="Generate AI test prompt for this file"
          className="inline-flex items-center justify-center rounded-md p-1 text-[var(--color-text-tertiary)] hover:text-[var(--color-model)] hover:bg-[var(--color-model-muted)] transition-colors"
        >
          <Sparkles className="h-3.5 w-3.5" />
        </button>
      ),
    },
  ];

  const activeColumns = columns.filter(
    (c): c is ResponsiveColumn<CoverageFileRow> => c !== null,
  );
  const fetchTruncated = summary.file_count > files.length;

  return (
    <div className="flex flex-col gap-6 sm:gap-8">
      <CoverageLede summary={summary} files={files} moduleCount={moduleCount} />

      <OverviewSection
        title="Health against coverage"
        description="Every instrumented file placed by its 0–10 defect-health score (higher is healthier) and its line coverage, sized by lines of code. The bottom-left quadrant is the one that costs money: code we score as weak, with no test watching it. Click a file to open its line-level heatmap."
      >
        <RiskCoverageScatter
          points={scatterPoints}
          onSelect={(p) => onOpenFile(p.file_path)}
        />
      </OverviewSection>

      {untested.length > 0 ? (
        <OverviewSection
          title="Untested hotspots"
          description="Files that change often or that much of the codebase depends on, with little or no coverage. A gap here is worth more than the same gap in code nobody touches."
        >
          <UntestedHotspotWarning entries={untested} onSelect={onOpenFile} />
        </OverviewSection>
      ) : null}

      <OverviewSection
        title="Module coverage"
        description="Directories rolled up under their top-level package, weighted by coverable lines and worst covered first. A directory with no coverable lines reads as no data rather than as a red zero."
      >
        <ModuleCoverageList modules={modules} />
      </OverviewSection>

      <OverviewSection
        title="Every file"
        description="Sorted worst first. Click a row for the line-level heatmap, or the prompt icon to draft a brief that has your AI agent write the missing tests."
        action={
          <input
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setVisible(PAGE);
            }}
            placeholder="Filter path…"
            aria-label="Filter files by path"
            className="w-48 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2 py-1.5 text-xs focus:border-[var(--color-border-hover)] focus:outline-none sm:w-56"
          />
        }
      >
        <div className="border-t border-[var(--color-border-default)]">
          <ResponsiveTable
            columns={activeColumns}
            rows={visibleFiles}
            rowKey={(f) => f.file_path}
            onRowClick={(f) => onOpenFile(f.file_path)}
            sortField={sortField}
            sortOrder={sortOrder}
            onSort={onSort}
            stacked="sm"
            bare
            empty={
              <EmptyState
                title="No files match"
                description="Adjust the path filter to see coverage rows."
              />
            }
          />
          {sortedFiles.length > 0 ? (
            <ResultsFooter
              shown={visibleFiles.length}
              total={sortedFiles.length}
              hasMore={visible < sortedFiles.length}
              onLoadMore={() => setVisible((v) => v + PAGE)}
              noun="files"
            />
          ) : null}
        </div>
        {fetchTruncated ? (
          <p className="text-xs text-[var(--color-text-tertiary)]">
            Showing {files.length.toLocaleString()} of{" "}
            {summary.file_count.toLocaleString()} instrumented files, capped so one
            tab does not pull the whole repository.
          </p>
        ) : null}
      </OverviewSection>

      {data.inferred ? <CoverageGap data={data} onOpenFile={onOpenFile} /> : null}
    </div>
  );
}

/**
 * The files the ingested coverage report never mentioned, answered by the graph.
 *
 * This is the hybrid shape: a report exists (so `basis` is `measured` and the
 * measured body above renders it), but the report's lcov source covered only a
 * subset of the repo. Every other file was invisible on the Tests tab — not
 * unindexed and not untested, just never named by the report. The graph knows
 * whether a test reaches those files, and this section says that.
 *
 * It honours the same rules as the pure-inferred view: counts only, no bar and
 * no percentage (reaching is a file-level fact with no line attribution), and
 * no health-band colour. `files_total` here is the count of non-measured files,
 * stated beside `measured_file_count` so the split is explicit.
 */
function CoverageGap({
  data,
  onOpenFile,
}: {
  data: HealthCoverageResponse;
  onOpenFile: (path: string) => void;
}) {
  const map = data.inferred as InferredTestMap;
  const measured = map.measured_file_count ?? 0;
  const unreached = map.files.filter((f) => !f.reached);

  return (
    <OverviewSection
      title="Files the report didn't cover"
      description={
        map.files_total > 0
          ? `Your coverage report named ${measured.toLocaleString()} files. The dependency graph answers for the other ${map.files_total.toLocaleString()}: ${map.files_reached.toLocaleString()} are reached by a test, ${map.files_not_reached.toLocaleString()} are not. Reaching is not executing, so treat it as a floor, not a measurement.`
          : "The dependency graph could not answer for the files the coverage report left out."
      }
    >
      <div className="border-t border-[var(--color-border-default)]">
        <ResponsiveTable
          columns={gapColumns}
          rows={unreached.slice(0, 50)}
          rowKey={(f) => f.file_path}
          onRowClick={(f) => onOpenFile(f.file_path)}
          stacked="sm"
          bare
          empty={
            <EmptyState
              title="Every file is reached"
              description="The graph found a test that reaches every file the coverage report didn't name."
            />
          }
        />
      </div>
    </OverviewSection>
  );
}

const gapColumns: ResponsiveColumn<ReachedFileRow>[] = [
  {
    key: "file_path",
    header: "File",
    priority: 1,
    render: (f) => (
      <span
        className="block truncate font-mono text-xs text-[var(--color-text-primary)]"
        title={f.file_path}
      >
        {f.file_path}
      </span>
    ),
  },
  {
    key: "reached",
    header: "Reached",
    priority: 1,
    render: (f) => (
      <span className="text-xs text-[var(--color-text-secondary)]">
        {f.reached ? "yes" : "no"}
      </span>
    ),
  },
  {
    key: "nloc",
    header: "Lines",
    priority: 3,
    align: "right",
    render: (f) => (
      <span className="tabular-nums text-[var(--color-text-tertiary)]">
        {f.nloc ?? "—"}
      </span>
    ),
  },
  {
    key: "health_score",
    header: "Health",
    priority: 2,
    align: "right",
    render: (f) =>
      f.health_score == null ? (
        <span className="text-[var(--color-text-tertiary)]">—</span>
      ) : (
        <span
          className={`inline-block rounded px-1.5 py-0.5 text-xs font-semibold ${scoreBadgeClass(f.health_score)}`}
        >
          {f.health_score.toFixed(1)}
        </span>
      ),
  },
];

/**
 * The last resort: no report, and the graph had nothing to say either — an
 * unindexed repository, or one with no test files at all. When the graph *can*
 * answer, `InferredTestsView` renders instead and the reader never reaches here.
 *
 * It used to be the only thing behind "no coverage", and it claimed "Nothing is
 * inferred: we read the lines your tests really executed" — which stopped being
 * true once the graph could answer, and turned a solved question into a wall.
 * What is left is the honest version: this is genuinely unknown, and here is
 * what would fill it.
 */
function NoCoverageState() {
  return (
    <div className="flex max-w-[62ch] flex-col gap-3">
      <h2 className="text-base font-semibold text-[var(--color-text-primary)]">
        Nothing here can say whether your code is tested
      </h2>
      <p className="text-[13px] leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
        No coverage report has been ingested, and the dependency graph found no
        test files to trace either. Either would fill this tab: a report gives the
        lines your tests executed, and the graph alone can name which tests reach
        which files with no setup at all. Run your suite with coverage on and hand
        us the report.
      </p>
      <pre className="w-fit overflow-x-auto rounded-md bg-[var(--color-bg-inset)] px-3 py-2 font-mono text-xs text-[var(--color-text-primary)]">
        pytest --cov --cov-report=lcov{"\n"}
        repowise coverage add coverage.lcov
      </pre>
      <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        LCOV · Cobertura · Clover
      </p>
    </div>
  );
}

/** Shapes and widths match the real layout, so nothing reflows when data lands. */
function CoverageSkeleton() {
  return (
    <div className="flex flex-col gap-6 sm:gap-8">
      <div className="flex flex-col gap-5 lg:flex-row lg:gap-12">
        <Skeleton className="h-[92px] w-full rounded-lg lg:w-[220px]" />
        <Skeleton className="h-[92px] w-full max-w-[62ch] rounded-lg" />
      </div>
      <Skeleton className="h-[74px] w-full" />
      <Skeleton className="h-[340px] w-full rounded-lg" />
      <Skeleton className="h-48 w-full rounded-lg" />
    </div>
  );
}
