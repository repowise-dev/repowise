"use client";

import { useState } from "react";
import type {
  BenchmarkRow,
  BenchmarkRun,
  EvidenceRunSummary,
  MeasuredRow,
  MeasuredRun,
} from "@repowise-dev/types/runtime-evidence";

import { formatNumber, formatPercent, formatRelativeTimeOrNull } from "../../lib/format";
import { CollapsibleSection } from "../../shared/collapsible-section";
import { FilePath } from "../../shared/file-path";
import { Segmented } from "../../shared/segmented";
import { Sparkline } from "../sparkline";
import { StatRibbon, type RibbonStat } from "../../stats/stat-ribbon";
import { Skeleton } from "../../ui/skeleton";
import type { AiPromptRequest } from "../ai-prompt-action";
import { buildMeasuredFunctionsPrompt } from "../ai-prompts/measured-functions-prompt";
import { PromptForRowsButton, RowPromptAction } from "../row-prompt-action";
import { MICRO } from "../tokens";
import type { PerformanceViewAdapter } from "./adapter";
import {
  intervalText,
  perOpText,
  plural,
  rowLocation,
  shareText,
  testsText,
  workloadClassLabel,
} from "./measured-format";
import { humanizeToken, symbolSubject } from "./presentation";
import { QUIET_LINK_CLS } from "./queue";
import {
  MEASURED_MAX,
  MEASURED_PREVIEW,
  useMeasuredRows,
  type RuntimeEvidenceView,
} from "./runtime";
import { RuntimeEvidenceNotices, runtimeNotices } from "./runtime-states";
import { overviewRun, RunOverview, SplitBar, type LowSignalPromptBuilder } from "./run-overview";
import {
  changeMark,
  changeText,
  commitPosition,
  POSITION_TAG,
  runHistory,
  runSeconds,
  runShares,
  secondsText,
  skippedText,
  truncatedCount,
  type RunHistory,
} from "./run-signal";
import { EvidenceWordTag } from "./tags";
import { TestsGroup } from "./test-costs";

/**
 * What stored profile runs measured, on the Performance tab: the run's status
 * strip, its ranked production functions, the unit-test runs apart, and every
 * stored run. A function with no row has no evidence, which is not the same
 * as cheap, so nothing here reads a missing value as zero.
 */

const sha7 = (sha: string | null) => (sha ? sha.slice(0, 7) : null);

/** Where the run spent its time: repo code, libraries, waits and the rest. Null without the split. */
export function RunSplit({ run }: { run: MeasuredRun | null }) {
  const shares = run ? runShares(run) : null;
  if (!run || !shares) return null;
  return (
    <div className="max-w-xs">
      <SplitBar shares={shares} legend label={`Where the ${run.workload_name} run spent its time`} />
    </div>
  );
}

/** The split for a lede's figure footer: null without a run that has one. */
export function runSplitFooter(run: MeasuredRun | null): React.ReactNode {
  return run?.repo_self_share != null ? <RunSplit run={run} /> : null;
}

/** The lede's sentence for the run: how much of it repo code can move. */
export function RunSplitSentence({ run }: { run: MeasuredRun | null }) {
  if (run?.repo_self_share == null) return null;
  return (
    <p className="mt-2.5">
      Repo code explains{" "}
      <strong className="font-semibold tabular-nums text-[var(--color-text-primary)]">
        {shareText(run.repo_self_share)}
        {run.samples != null ? ` of ${formatNumber(run.samples)} samples` : ""}
      </strong>{" "}
      in <code className="font-mono text-[0.9em]">{run.workload_name}</code>. The rest is libraries,
      waits and time outside indexed code, so a fix in repo code moves at most that share of this run.
    </p>
  );
}

function commitSub(run: MeasuredRun, summary: EvidenceRunSummary | undefined): string | undefined {
  const at = sha7(run.commit);
  if (!at) return undefined;
  if (summary?.freshness === "current") return `at ${at}, the indexed commit`;
  if (summary?.freshness === "stale") return `at ${at}, not the indexed commit`;
  return `at ${at}`;
}

function stripStats(run: MeasuredRun, runs: EvidenceRunSummary[] | undefined): RibbonStat[] {
  const summary = runs?.find((r) => r.run_id === run.run_id);
  const kinds = Array.from(new Set((runs ?? []).map((r) => humanizeToken(r.kind).toLowerCase())));
  return [
    {
      label: "Workload",
      value: run.workload_name,
      sub: [workloadClassLabel(run.workload_class), summary?.tool].filter(Boolean).join(" · "),
    },
    {
      label: "Repo code explains",
      value: run.repo_self_share != null ? shareText(run.repo_self_share) : "",
      sub: run.samples != null ? `of ${formatNumber(run.samples)} samples` : undefined,
    },
    {
      label: "Measured",
      value: formatRelativeTimeOrNull(run.measured_at, ""),
      sub: commitSub(run, summary),
    },
    {
      label: "Runs stored",
      value: runs ? plural(runs.length, "run") : "",
      sub: kinds.join(", ") || undefined,
    },
  ];
}

/** The meter: the share against one scale for the whole list, with its interval. */
function ShareMeter({ row, scale }: { row: MeasuredRow; scale: number }) {
  if (row.share == null || scale <= 0) return null;
  const pct = (v: number) => `${Math.max(0, Math.min(100, (v / scale) * 100))}%`;
  return (
    <div className="relative mt-1.5 h-1 rounded-full bg-[var(--color-bg-inset)]" aria-hidden="true">
      <span
        className="absolute inset-y-0 left-0 rounded-full bg-[var(--color-text-secondary)]"
        style={{ width: pct(row.share) }}
      />
      {row.share_low != null && row.share_high != null ? (
        <span
          className="absolute -inset-y-0.5 border-x border-[var(--color-text-tertiary)]"
          style={{ left: pct(row.share_low), width: pct(row.share_high - row.share_low) }}
        />
      ) : null}
    </div>
  );
}

function RowMeta({ row, run }: { row: MeasuredRow; run: MeasuredRun }) {
  const parts: string[] = [];
  if (row.callers_total != null) parts.push(plural(row.callers_total, "caller"));
  if (row.owner) {
    parts.push(
      row.owner.line_pct != null
        ? `${row.owner.name} ${formatPercent(row.owner.line_pct)}`
        : row.owner.name,
    );
  }
  parts.push(testsText(row.tests));
  if (row.freshness === "body_unchanged" && run.commit) {
    parts.push(`body unchanged since ${sha7(run.commit)}`);
  }
  return (
    <p className="mt-1.5 flex flex-wrap gap-x-1.5 gap-y-0.5 font-mono text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
      {parts.map((part, i) => (
        <span key={i} className="inline-flex gap-x-1.5">
          {i > 0 ? <span aria-hidden="true">·</span> : null}
          {part}
        </span>
      ))}
    </p>
  );
}

/** "self 22.0 s · total 76.9 s" when the run's total is a time, else "total 6.8%". */
function timeText(row: MeasuredRow, seconds: number | null): string | null {
  if (row.share == null) return null;
  const total =
    row.inclusive != null
      ? `total ${seconds != null ? secondsText(row.inclusive * seconds) : shareText(row.inclusive)}`
      : null;
  const self = seconds != null ? `self ${secondsText(row.share * seconds)}` : null;
  return [self, total].filter(Boolean).join(" · ") || null;
}

function MeasuredRowItem({
  row,
  run,
  scale,
  seconds,
  href,
  onOpenCause,
  onPrompt,
}: {
  row: MeasuredRow;
  run: MeasuredRun;
  scale: number;
  /** The run's total in seconds, when its unit is a time. */
  seconds: number | null;
  /** Where the location links: the host's symbol page, else its file. */
  href: string;
  onOpenCause?: ((opportunityId: string) => void) | undefined;
  onPrompt: (row: MeasuredRow) => void;
}) {
  const time = timeText(row, seconds);
  const name = symbolSubject(row.symbol_id);
  const findings = row.on_static_findings ?? [];
  const interval = intervalText(row.share_low, row.share_high);
  return (
    <li className="group/row grid grid-cols-[28px_minmax(0,1fr)] gap-x-3 gap-y-2 px-1 py-3.5 hover:bg-[var(--color-bg-elevated)] lg:grid-cols-[28px_minmax(0,1fr)_210px_auto] lg:items-start">
      <span className="pt-0.5 text-right font-mono text-xs tabular-nums text-[var(--color-text-tertiary)]">
        {formatNumber(row.rank)}
      </span>
      <div className="min-w-0">
        <p className="font-mono text-[13px] font-medium text-[var(--color-text-primary)] [overflow-wrap:anywhere]">
          {name}
        </p>
        <a href={href} className={`mt-0.5 flex min-w-0 font-mono text-xs ${QUIET_LINK_CLS}`}>
          <FilePath path={rowLocation(row)} fold className="text-xs" />
        </a>
        <RowMeta row={row} run={run} />
        <p className="mt-1.5">
          {findings.length > 0 ? (
            <EvidenceWordTag
              ink
              onClick={onOpenCause ? () => onOpenCause(findings[0]!) : undefined}
              label={findings.length > 1 ? `Open the first of ${findings.length} findings` : undefined}
            >
              {plural(findings.length, "finding")}
            </EvidenceWordTag>
          ) : (
            <EvidenceWordTag>No static finding</EvidenceWordTag>
          )}
        </p>
      </div>
      <div className="col-start-2 min-w-0 lg:col-start-auto">
        {run.ordering_only || row.share == null ? (
          <span className="font-mono text-xs text-[var(--color-text-tertiary)]">
            rank {formatNumber(row.rank)}
          </span>
        ) : (
          <>
            <span className="font-mono text-[13px] font-medium tabular-nums text-[var(--color-text-primary)]">
              {shareText(row.share)}
            </span>{" "}
            <span className="font-mono text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
              {[interval, row.samples != null ? plural(row.samples, "sample") : null]
                .filter(Boolean)
                .join(" · ")}
            </span>
            {time ? (
              <span className="block font-mono text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
                {time}
              </span>
            ) : null}
            <ShareMeter row={row} scale={scale} />
          </>
        )}
      </div>
      <div className="col-start-2 flex items-center lg:col-start-auto lg:justify-end">
        <RowPromptAction onClick={() => onPrompt(row)} subject={name} />
      </div>
    </li>
  );
}

/** The rows' heading and description, with the prompt for the rows shown. */
function RowsHead({
  heading,
  description,
  count,
  onPromptAll,
}: {
  heading: string | null;
  description: string;
  count: number;
  onPromptAll: () => void;
}) {
  return (
    <div className="mb-3 flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
      <div className="min-w-0">
        {heading ? (
          <h3 className="text-lg font-semibold text-[var(--color-text-primary)]">{heading}</h3>
        ) : null}
        <p className="mt-1 max-w-[70ch] text-xs text-[var(--color-text-secondary)]">{description}</p>
      </div>
      <PromptForRowsButton
        count={count}
        label={(n) => (n === 1 ? "Copy prompt for this one" : `Copy prompt for the top ${formatNumber(n)}`)}
        onClick={onPromptAll}
      />
    </div>
  );
}

/** What the list leaves out and how its bars read, as one caption. */
function rowsCaption(run: MeasuredRun, shown: number, total: number): string {
  const outside = run.outside_production ?? 0;
  return [
    total > shown ? `Showing ${formatNumber(shown)} of ${plural(total, "ranked function")}.` : null,
    outside > 0
      ? `${plural(outside, "function")} measured in test and tooling code ${outside === 1 ? "is" : "are"} counted, not listed.`
      : null,
    run.repo_self_share != null && !run.ordering_only
      ? "Bars are scaled to the repo-code share of the run."
      : null,
  ]
    .filter(Boolean)
    .join(" ");
}

function RowsFooter({
  caption,
  showAll,
}: {
  caption: string;
  /** "Show all N", when more rows are ranked than shown. */
  showAll: { count: number; onClick: () => void } | null;
}) {
  return (
    <p className="mt-2.5 pl-10 text-xs text-[var(--color-text-tertiary)]">
      {caption}{" "}
      {showAll ? (
        <button type="button" onClick={showAll.onClick} className={QUIET_LINK_CLS}>
          Show all {formatNumber(showAll.count)}
        </button>
      ) : null}
    </p>
  );
}

/** The list while it loads, when it could not be read, or nothing when it is empty. */
function RowsPending({ loading, what = "the ranked functions" }: { loading: boolean; what?: string }) {
  if (loading) {
    return (
      <div className="space-y-px" aria-busy="true">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-[72px] rounded-none" />
        ))}
      </div>
    );
  }
  return (
    <p role="status" className="text-sm text-[var(--color-text-tertiary)]">
      Could not read {what}. Reload to try again.
    </p>
  );
}

/**
 * One run's ranked functions with a "Show all" past the preview, under a
 * heading whose action is the agent prompt for the rows shown. Keyed by run
 * at the call site, so picking another workload starts at the preview again.
 */
function MeasuredRows({
  adapter,
  run,
  seconds,
  heading,
  description,
  onOpenCause,
  onPrompt,
}: {
  adapter: PerformanceViewAdapter;
  run: MeasuredRun;
  /** The run's total in seconds, when its unit is a time. */
  seconds: number | null;
  /** The heading, or null where a disclosure already names the rows. */
  heading: string | null;
  description: string;
  onOpenCause?: ((opportunityId: string) => void) | undefined;
  onPrompt: (request: AiPromptRequest) => void;
}) {
  const [all, setAll] = useState(false);
  const { data, error, isLoading } = useMeasuredRows(
    adapter,
    run.run_id,
    all ? MEASURED_MAX : MEASURED_PREVIEW,
  );
  const group = data?.measured ?? data?.measured_in_tests ?? null;
  const rows = group?.rows ?? [];
  const total = group?.total ?? 0;
  // One scale for every bar: the repo-code share of the run, else the widest interval.
  const scale =
    run.repo_self_share ?? Math.max(0, ...rows.map((r) => r.share_high ?? r.share ?? 0));
  const promptFor = (subset: MeasuredRow[]): AiPromptRequest => ({
    title: "Prompt for your agent",
    description: `${plural(subset.length, "function")} measured in ${run.workload_name}, in rank order.`,
    filePath: subset.length === 1 ? rowLocation(subset[0]!) : null,
    getPrompt: (flavor) => buildMeasuredFunctionsPrompt({ run, rows: subset, flavor }),
  });
  // An error wins over the rows kept from the last read: a failed "Show all"
  // must not leave the preview looking like the whole list.
  const pending = Boolean(error) || (!data && isLoading);
  if (!pending && rows.length === 0) return null;
  return (
    <div>
      <RowsHead
        heading={heading}
        description={description}
        count={rows.length}
        onPromptAll={() => onPrompt(promptFor(rows))}
      />
      {pending ? (
        <RowsPending loading={isLoading} />
      ) : (
        <>
          <ul
            aria-label={`Measured in ${run.workload_name}`}
            className="divide-y divide-[var(--color-border-default)] border-y border-[var(--color-border-default)]"
          >
            {rows.map((row) => (
              <MeasuredRowItem
                key={`${row.run_id}:${row.symbol_id}`}
                row={row}
                run={run}
                scale={scale}
                seconds={seconds}
                href={adapter.symbolHref?.(row.symbol_id) ?? adapter.fileHref(row.file_path)}
                onOpenCause={onOpenCause}
                onPrompt={(one) => onPrompt(promptFor([one]))}
              />
            ))}
          </ul>
          <RowsFooter
            caption={rowsCaption(run, rows.length, total)}
            showAll={
              total > rows.length && !all
                ? { count: Math.min(total, MEASURED_MAX), onClick: () => setAll(true) }
                : null
            }
          />
        </>
      )}
    </div>
  );
}

/** One benchmark: its name, the production function it measures when known, its figures per operation. */
function BenchmarkRowItem({ row, adapter }: { row: BenchmarkRow; adapter: PerformanceViewAdapter }) {
  const figures = perOpText(row);
  const measures = row.measures;
  return (
    <li className="grid grid-cols-[minmax(0,1fr)] gap-x-3 gap-y-1 px-1 py-3 hover:bg-[var(--color-bg-elevated)] lg:grid-cols-[minmax(0,1fr)_auto] lg:items-start">
      <div className="min-w-0">
        <p className="font-mono text-[13px] font-medium text-[var(--color-text-primary)] [overflow-wrap:anywhere]">
          {row.benchmark}
        </p>
        {measures ? (
          <p className="mt-0.5 text-xs text-[var(--color-text-tertiary)]">
            measures{" "}
            <a
              href={adapter.symbolHref?.(measures) ?? adapter.fileHref(measures.split("::")[0] ?? measures)}
              className={`font-mono ${QUIET_LINK_CLS}`}
            >
              {symbolSubject(measures)}
            </a>
          </p>
        ) : null}
      </div>
      {figures ? (
        <p className="font-mono text-[13px] tabular-nums text-[var(--color-text-primary)] lg:text-right">
          {figures}
        </p>
      ) : null}
    </li>
  );
}

/** One benchmark run's results under its workload and commit, with a "Show all" past the preview. */
function BenchmarkRunRows({ adapter, run }: { adapter: PerformanceViewAdapter; run: BenchmarkRun }) {
  const [all, setAll] = useState(false);
  const { data, error, isLoading } = useMeasuredRows(
    adapter,
    run.run_id,
    all ? MEASURED_MAX : MEASURED_PREVIEW,
  );
  const rows = data?.benchmarks?.rows ?? [];
  const total = data?.benchmarks?.total ?? 0;
  const pending = Boolean(error) || (!data && isLoading);
  if (!pending && rows.length === 0) return null;
  const at = sha7(run.commit);
  const label = [
    run.workload_name,
    at ? `at ${at}` : null,
    run.measured_at ? formatRelativeTimeOrNull(run.measured_at) : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <div>
      <p className="mb-1.5 font-mono text-xs text-[var(--color-text-secondary)] [overflow-wrap:anywhere]">
        {label}
      </p>
      {pending ? (
        <RowsPending loading={isLoading} what="the benchmark results" />
      ) : (
        <>
          <ul
            aria-label={`Benchmarks in ${run.workload_name}`}
            className="divide-y divide-[var(--color-border-default)] border-y border-[var(--color-border-default)]"
          >
            {rows.map((row) => (
              <BenchmarkRowItem key={`${row.run_id}:${row.benchmark_symbol_id ?? ""}:${row.benchmark}`} row={row} adapter={adapter} />
            ))}
          </ul>
          {total > rows.length ? (
            <RowsFooter
              caption={`Showing ${formatNumber(rows.length)} of ${plural(total, "benchmark")}.`}
              showAll={all ? null : { count: Math.min(total, MEASURED_MAX), onClick: () => setAll(true) }}
            />
          ) : null}
        </>
      )}
    </div>
  );
}

/**
 * Each workload's newest benchmark run: per benchmark, the function it
 * measures and its figures per operation. A benchmark times one piece of
 * work in isolation, so it never ranks functions or reorders the queue.
 */
function BenchmarksGroup({ view, adapter }: { view: RuntimeEvidenceView; adapter: PerformanceViewAdapter }) {
  if (view.benchmarkRuns.length === 0) return null;
  return (
    <div>
      <h3 className="text-lg font-semibold text-[var(--color-text-primary)]">Benchmarks</h3>
      <p className="mb-3 mt-1 max-w-[70ch] text-xs text-[var(--color-text-secondary)]">
        Each benchmark&apos;s time per operation and the production function it measures. A
        benchmark times one piece of work on its own, so it does not rank functions or reorder the
        queue.
      </p>
      <div className="space-y-6">
        {view.benchmarkRuns.map((run) => (
          <BenchmarkRunRows key={run.run_id} adapter={adapter} run={run} />
        ))}
      </div>
    </div>
  );
}

function runResult(run: EvidenceRunSummary): string {
  const value =
    run.total_value != null
      ? `${formatNumber(Math.round(run.total_value * 100) / 100)}${run.unit ? ` ${run.unit}` : ""}`
      : null;
  const failed = run.tests_failed ? `${formatNumber(run.tests_failed)} failed` : null;
  return [value, failed].filter(Boolean).join(", ") || "—";
}

function runMapped(run: EvidenceRunSummary): string {
  const { matched, total } = run.units;
  if (matched == null || total == null) return "—";
  return `${formatNumber(matched)} of ${formatNumber(total)}`;
}

/** Why the rest did not map, "16 outside repo, 5 vendored", with the row cap named apart. */
function runUnmapped(run: EvidenceRunSummary): string | null {
  const cut = truncatedCount(run);
  const rest = Object.fromEntries(
    Object.entries(run.skipped ?? {}).filter(([reason]) => reason !== "units_truncated"),
  );
  return (
    [skippedText(rest), cut > 0 ? `${formatNumber(cut)} cut at the row cap` : null]
      .filter(Boolean)
      .join("; ") || null
  );
}

function ChangeCell({ run, history }: { run: EvidenceRunSummary; history: RunHistory }) {
  const change = history.changes.get(run.run_id);
  if (!change) return null;
  const mark = changeMark(change);
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      {changeText(change, run.unit)}
      {mark ? <EvidenceWordTag ink={mark === "slower"}>{mark}</EvidenceWordTag> : null}
    </span>
  );
}

/** The workload's totals over its stored runs, on its newest run's row; the words are its text. */
function TrendCell({ run, history }: { run: EvidenceRunSummary; history: RunHistory }) {
  const values = history.trends.get(run.run_id);
  if (!values) return null;
  const say = (v: number) => {
    const seconds = runSeconds({ total_value: v, unit: run.unit });
    return seconds != null ? secondsText(seconds) : `${formatNumber(Math.round(v))} ${run.unit ?? ""}`.trim();
  };
  return (
    <span className="inline-flex items-center text-[var(--color-text-tertiary)]">
      <Sparkline values={values} width={64} height={16} />
      <span className="sr-only">
        {`${values.length} runs, oldest ${say(values[0]!)}, newest ${say(values[values.length - 1]!)}`}
      </span>
    </span>
  );
}

const TH =
  "whitespace-nowrap px-2.5 pb-2 pt-2.5 text-left font-mono text-[10px] font-normal uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";
const TD = "px-2.5 py-2.5 align-top text-[var(--color-text-secondary)]";
const RUN_COLUMNS = [
  "Workload",
  "Kind",
  "Class",
  "Tool",
  "Commit",
  "Mapped",
  "Result",
  "Change",
  "Trend",
  "Added",
];

/** Every stored run: what it measured, against which commit, how it mapped and how it moved. */
function RunsTable({ runs, reference }: { runs: EvidenceRunSummary[]; reference: string | null }) {
  const history = runHistory(runs);
  return (
    <div className="overflow-x-auto border-t border-[var(--color-border-default)]">
      <table className="w-full min-w-[960px] border-collapse text-xs">
        <thead>
          <tr className="border-b border-[var(--color-border-default)]">
            {RUN_COLUMNS.map((h) => (
              <th key={h} scope="col" className={TH}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--color-border-default)]">
          {runs.map((run) => {
            const tag = POSITION_TAG[commitPosition(run, runs, reference)];
            const unmapped = runUnmapped(run);
            return (
              <tr key={run.run_id}>
                <td className={`${TD} font-mono text-[var(--color-text-primary)] [overflow-wrap:anywhere]`}>
                  {run.workload}
                </td>
                <td className={TD}>{humanizeToken(run.kind).toLowerCase()}</td>
                <td className={TD}>{workloadClassLabel(run.class)}</td>
                <td className={`${TD} font-mono`}>
                  {[run.tool, run.tool_version].filter(Boolean).join(" ") || "—"}
                </td>
                <td className={`${TD} whitespace-nowrap font-mono`}>
                  {sha7(run.commit_sha) ?? "—"}
                  {tag ? (
                    <span className="ml-1.5">
                      <EvidenceWordTag>{tag}</EvidenceWordTag>
                    </span>
                  ) : null}
                </td>
                <td className={`${TD} font-mono tabular-nums`}>
                  <span className="whitespace-nowrap">{runMapped(run)}</span>
                  {run.mapping_partial ? (
                    <span className="ml-1.5">
                      <EvidenceWordTag>partial</EvidenceWordTag>
                    </span>
                  ) : null}
                  {unmapped ? (
                    <span className="mt-0.5 block font-sans text-[11px] text-[var(--color-text-tertiary)]">
                      {unmapped}
                    </span>
                  ) : null}
                </td>
                <td className={`${TD} font-mono tabular-nums`}>{runResult(run)}</td>
                <td className={`${TD} whitespace-nowrap font-mono tabular-nums`}>
                  <ChangeCell run={run} history={history} />
                </td>
                <td className={TD}>
                  <TrendCell run={run} history={history} />
                </td>
                <td className={`${TD} whitespace-nowrap`}>{formatRelativeTimeOrNull(run.ingested_at)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** A disclosure's title, with its details on a caption line that wraps at any width. */
const disclosureTitle = (title: string, caption?: string) => (
  <>
    <span className="text-[15px] font-semibold text-[var(--color-text-primary)]">{title}</span>
    {caption ? (
      <span className="block text-xs font-normal text-[var(--color-text-tertiary)] [overflow-wrap:anywhere]">
        {caption}
      </span>
    ) : null}
  </>
);

type CauseOpener = ((opportunityId: string) => void) | undefined;

/** The run's total in seconds from its header, when the header is read and its unit is a time. */
function secondsOf(view: RuntimeEvidenceView, runId: string): number | null {
  const summary = view.runs?.find((r) => r.run_id === runId);
  return summary ? runSeconds(summary) : null;
}

/** The picked workload: its strip with the lens and its header, then its ranked functions. */
function LeadRun({
  view,
  adapter,
  onOpenCause,
  onPrompt,
  lowSignalPrompt,
}: {
  view: RuntimeEvidenceView;
  adapter: PerformanceViewAdapter;
  onOpenCause: CauseOpener;
  onPrompt: (request: AiPromptRequest) => void;
  lowSignalPrompt: LowSignalPromptBuilder | undefined;
}) {
  const { run, leads, pick, runs } = view;
  if (!run) return null;
  const summary = overviewRun(runs, run, view.referenceCommit);
  return (
    <>
      <div>
        <div className="mb-2.5 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <p className={MICRO}>Runtime evidence</p>
          {leads.length > 1 ? (
            <Segmented
              label="Workload"
              value={run.run_id}
              options={leads.map((r) => ({
                value: r.run_id,
                label: r.workload_name,
                count: workloadClassLabel(r.workload_class),
              }))}
              onChange={pick}
            />
          ) : null}
        </div>
        <StatRibbon stats={stripStats(run, runs)} />
        {summary && runs ? (
          <div className="mt-4">
            <RunOverview
              summary={summary}
              measured={run}
              runs={runs}
              reference={view.referenceCommit}
              lowSignalPrompt={lowSignalPrompt}
            />
          </div>
        ) : null}
      </div>
      <MeasuredRows
        key={run.run_id}
        adapter={adapter}
        run={run}
        seconds={secondsOf(view, run.run_id)}
        heading={`Measured cost in ${run.workload_name}`}
        description="Production functions ranked by self time, waits excluded; total adds what each one calls. A share is of the whole run, with its interval. A function with no row has no evidence, which is not the same as cheap."
        onOpenCause={onOpenCause}
        onPrompt={onPrompt}
      />
    </>
  );
}

/** The newest unit-test run, closed: ordered within the test run only. */
function TestRunGroup({
  view,
  adapter,
  onOpenCause,
  onPrompt,
}: {
  view: RuntimeEvidenceView;
  adapter: PerformanceViewAdapter;
  onOpenCause: CauseOpener;
  onPrompt: (request: AiPromptRequest) => void;
}) {
  const { testRun, testsTotal } = view;
  if (!testRun) return null;
  const caption = [
    testRun.workload_name,
    "unit test",
    testRun.samples != null ? plural(testRun.samples, "sample") : null,
    testsTotal > 0 ? plural(testsTotal, "ranked function") : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <CollapsibleSection title={disclosureTitle("Measured in tests", caption)}>
      <div className="pb-4">
        <MeasuredRows
          key={testRun.run_id}
          adapter={adapter}
          run={testRun}
          seconds={secondsOf(view, testRun.run_id)}
          heading={null}
          description="Order within the test run only. A unit-test run never leads the queue: test fixtures decide what is costly there."
          onOpenCause={onOpenCause}
          onPrompt={onPrompt}
        />
      </div>
    </CollapsibleSection>
  );
}

function RunsGroup({
  runs,
  reference,
}: {
  runs: EvidenceRunSummary[] | undefined;
  reference: string | null;
}) {
  if (!runs || runs.length === 0) return null;
  return (
    <CollapsibleSection
      title={disclosureTitle("Runs", "Newest first, each with its change from the run before")}
      hint={`${formatNumber(runs.length)} stored`}
    >
      <div className="pb-3">
        <RunsTable runs={runs} reference={reference} />
      </div>
    </CollapsibleSection>
  );
}

/**
 * The runtime evidence block between the lede and the queue: why stored runs
 * rank nothing when they do not, the strip with the workload lens and the
 * run's header, the measured table, each benchmark's results, then the unit-test run, the test time and
 * every stored run, closed. With no ranked workload the newest profile's
 * header still shows: its total, its split, and whether it carries enough
 * signal to rank. Renders nothing when there is nothing to say.
 */
export function RuntimeEvidenceSection({
  view,
  adapter,
  onOpenCause: openCause,
  canOpenCause = true,
  onPrompt,
  connectPrompt,
  lowSignalPrompt,
}: {
  view: RuntimeEvidenceView;
  adapter: PerformanceViewAdapter;
  onOpenCause?: CauseOpener;
  /** False when the host cannot open a cause by id, so a findings tag stays a word. */
  canOpenCause?: boolean;
  onPrompt: (request: AiPromptRequest) => void;
  /** The host's connect-evidence prompt, for a notice that asks for more evidence. */
  connectPrompt?: (() => string) | undefined;
  /** The host's prompt for collecting a profile that ranks; omitted, the built-in one. */
  lowSignalPrompt?: LowSignalPromptBuilder | undefined;
}) {
  const onOpenCause = canOpenCause ? openCause : undefined;
  const notices = runtimeNotices(view);
  const unranked = view.run ? null : overviewRun(view.runs, null, view.referenceCommit);
  if (
    !view.run &&
    !unranked &&
    !view.testRun &&
    !view.tests &&
    view.benchmarkRuns.length === 0 &&
    notices.length === 0
  )
    return null;
  const rows = { view, adapter, onOpenCause, onPrompt };
  return (
    <section aria-label="Runtime evidence" className="space-y-10">
      <RuntimeEvidenceNotices notices={notices} connectPrompt={connectPrompt} />
      {unranked && view.runs ? (
        <RunOverview
          heading
          summary={unranked}
          measured={null}
          runs={view.runs}
          reference={view.referenceCommit}
          lowSignalPrompt={lowSignalPrompt}
        />
      ) : null}
      <LeadRun {...rows} lowSignalPrompt={lowSignalPrompt} />
      <BenchmarksGroup view={view} adapter={adapter} />
      <div className="border-b border-[var(--color-border-default)]">
        <TestRunGroup {...rows} />
        <TestsGroup tests={view.tests} fileHref={adapter.fileHref} onPrompt={onPrompt} />
        <RunsGroup runs={view.runs} reference={view.referenceCommit} />
      </div>
    </section>
  );
}
