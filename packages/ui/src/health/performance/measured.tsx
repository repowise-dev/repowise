"use client";

import { useState } from "react";
import type {
  EvidenceRunSummary,
  MeasuredRow,
  MeasuredRun,
  SlowTest,
} from "@repowise-dev/types/runtime-evidence";

import { formatNumber, formatPercent, formatRelativeTimeOrNull } from "../../lib/format";
import { CollapsibleSection } from "../../shared/collapsible-section";
import { FilePath } from "../../shared/file-path";
import { ProportionBar } from "../../shared/proportion-bar";
import { Segmented } from "../../shared/segmented";
import { StatRibbon, type RibbonStat } from "../../stats/stat-ribbon";
import { Skeleton } from "../../ui/skeleton";
import type { AiPromptRequest } from "../ai-prompt-action";
import { buildMeasuredFunctionsPrompt } from "../ai-prompts/measured-functions-prompt";
import { PromptForRowsButton, RowPromptAction } from "../row-prompt-action";
import { MICRO } from "../tokens";
import type { PerformanceViewAdapter } from "./adapter";
import {
  intervalText,
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
import { EvidenceWordTag } from "./tags";

/**
 * What stored profile runs measured, on the Performance tab: the run's status
 * strip, its ranked production functions, the unit-test runs apart, and every
 * stored run. A function with no row has no evidence, which is not the same
 * as cheap, so nothing here reads a missing value as zero.
 */

const sha7 = (sha: string | null) => (sha ? sha.slice(0, 7) : null);

/** Neutral ink for a run's split: green, amber and red stay with health. */
const SPLIT_INK = {
  repo: "var(--color-text-primary)",
  library: "color-mix(in srgb, var(--color-text-tertiary) 55%, var(--color-bg-inset))",
  wait: "color-mix(in srgb, var(--color-text-tertiary) 28%, var(--color-bg-inset))",
} as const;

/** Where the run spent its time: repo code, libraries, waits. Null without the split. */
export function RunSplit({ run }: { run: MeasuredRun | null }) {
  if (run?.repo_self_share == null) return null;
  const parts = [
    { key: "repo", label: "Repo code", value: run.repo_self_share },
    { key: "library", label: "Libraries", value: run.library_share },
    { key: "wait", label: "Waiting", value: run.wait_share },
  ] as const;
  const segments = parts.flatMap((p) =>
    p.value != null && p.value > 0
      ? [{ key: p.key, label: p.label, value: p.value, detail: shareText(p.value), color: SPLIT_INK[p.key] }]
      : [],
  );
  if (segments.length === 0) return null;
  return (
    <div className="max-w-xs">
      <ProportionBar
        segments={segments}
        sort={false}
        size="sm"
        label={`Where the ${run.workload_name} run spent its time`}
      />
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
      in <code className="font-mono text-[0.9em]">{run.workload_name}</code>. The rest is libraries
      and waits, so a fix in repo code moves at most that share of this run.
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
      row.owner.commit_pct != null
        ? `${row.owner.name} ${formatPercent(row.owner.commit_pct)}`
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

function MeasuredRowItem({
  row,
  run,
  scale,
  onOpenCause,
  onPrompt,
}: {
  row: MeasuredRow;
  run: MeasuredRun;
  scale: number;
  onOpenCause?: ((opportunityId: string) => void) | undefined;
  onPrompt: (row: MeasuredRow) => void;
}) {
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
        <p className="mt-0.5 flex min-w-0 font-mono text-xs text-[var(--color-text-secondary)]">
          <FilePath path={rowLocation(row)} fold className="text-xs" />
        </p>
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
function RowsPending({ loading }: { loading: boolean }) {
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
      Could not read the ranked functions. Reload to try again.
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
  heading,
  description,
  onOpenCause,
  onPrompt,
}: {
  adapter: PerformanceViewAdapter;
  run: MeasuredRun;
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

const TH =
  "whitespace-nowrap px-2.5 pb-2 pt-2.5 text-left font-mono text-[10px] font-normal uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";
const TD = "px-2.5 py-2.5 align-top text-[var(--color-text-secondary)]";
const RUN_COLUMNS = ["Workload", "Kind", "Class", "Tool", "Commit", "Mapped", "Result", "Added"];

/** Every stored run: what it measured, against which commit, and how it mapped. */
function RunsTable({ runs }: { runs: EvidenceRunSummary[] }) {
  return (
    <div className="overflow-x-auto border-t border-[var(--color-border-default)]">
      <table className="w-full min-w-[720px] border-collapse text-xs">
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
          {runs.map((run) => (
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
                {run.freshness === "stale" ? (
                  <span className="ml-1.5">
                    <EvidenceWordTag>other commit</EvidenceWordTag>
                  </span>
                ) : null}
              </td>
              <td className={`${TD} whitespace-nowrap font-mono tabular-nums`}>
                {runMapped(run)}
                {run.mapping_partial ? (
                  <span className="ml-1.5">
                    <EvidenceWordTag>partial</EvidenceWordTag>
                  </span>
                ) : null}
              </td>
              <td className={`${TD} font-mono tabular-nums`}>{runResult(run)}</td>
              <td className={`${TD} whitespace-nowrap`}>{formatRelativeTimeOrNull(run.ingested_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The slowest timed tests, longest first, with the workload that timed each. */
function SlowTests({ tests }: { tests: readonly SlowTest[] }) {
  return (
    <ul className="divide-y divide-[var(--color-border-default)] border-y border-[var(--color-border-default)] text-xs">
      {tests.map((t) => (
        <li key={`${t.workload}:${t.test_id}`} className="flex items-baseline gap-3 px-1 py-2.5">
          <span className="min-w-0 flex-1 font-mono text-[var(--color-text-primary)] [overflow-wrap:anywhere]">
            {t.test_id}
          </span>
          {t.outcome && t.outcome !== "passed" ? <EvidenceWordTag>{t.outcome}</EvidenceWordTag> : null}
          <span className="shrink-0 font-mono tabular-nums text-[var(--color-text-secondary)]">
            {t.duration_s < 0.01 ? "under 0.01" : formatNumber(Math.round(t.duration_s * 100) / 100)} s
          </span>
        </li>
      ))}
    </ul>
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

/** The picked workload: its strip with the lens, then its ranked functions. */
function LeadRun({
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
  const { run, leads, pick, runs } = view;
  if (!run) return null;
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
      </div>
      <MeasuredRows
        key={run.run_id}
        adapter={adapter}
        run={run}
        heading={`Measured cost in ${run.workload_name}`}
        description="Production functions ranked by self time, waits excluded. A share is of the whole run, with its interval. A function with no row has no evidence, which is not the same as cheap."
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
          heading={null}
          description="Order within the test run only. A unit-test run never leads the queue: test fixtures decide what is costly there."
          onOpenCause={onOpenCause}
          onPrompt={onPrompt}
        />
      </div>
    </CollapsibleSection>
  );
}

function SlowTestsGroup({ slowTests }: { slowTests: RuntimeEvidenceView["slowTests"] }) {
  if (!slowTests) return null;
  return (
    <CollapsibleSection
      title={disclosureTitle("Slow tests", `Longest first, of ${plural(slowTests.total, "timed test")}`)}
      hint={plural(slowTests.tests.length, "test")}
    >
      <div className="pb-3">
        <SlowTests tests={slowTests.tests} />
      </div>
    </CollapsibleSection>
  );
}

function RunsGroup({ runs }: { runs: EvidenceRunSummary[] | undefined }) {
  if (!runs || runs.length === 0) return null;
  return (
    <CollapsibleSection
      title={disclosureTitle("Runs", "Newest first")}
      hint={`${formatNumber(runs.length)} stored`}
    >
      <div className="pb-3">
        <RunsTable runs={runs} />
      </div>
    </CollapsibleSection>
  );
}

/**
 * The runtime evidence block between the lede and the queue: why stored runs
 * rank nothing when they do not, the strip with the workload lens, the
 * measured table, then the unit-test run, the slowest tests and every stored
 * run, closed. Renders nothing when there is nothing to say.
 */
export function RuntimeEvidenceSection({
  view,
  adapter,
  onOpenCause: openCause,
  canOpenCause = true,
  onPrompt,
}: {
  view: RuntimeEvidenceView;
  adapter: PerformanceViewAdapter;
  onOpenCause?: CauseOpener;
  /** False when the host cannot open a cause by id, so a findings tag stays a word. */
  canOpenCause?: boolean;
  onPrompt: (request: AiPromptRequest) => void;
}) {
  const onOpenCause = canOpenCause ? openCause : undefined;
  const notices = runtimeNotices(view);
  if (!view.run && !view.testRun && !view.slowTests && notices.length === 0) return null;
  const rows = { view, adapter, onOpenCause, onPrompt };
  return (
    <section aria-label="Runtime evidence" className="space-y-10">
      <RuntimeEvidenceNotices notices={notices} />
      <LeadRun {...rows} />
      <div className="border-b border-[var(--color-border-default)]">
        <TestRunGroup {...rows} />
        <SlowTestsGroup slowTests={view.slowTests} />
        <RunsGroup runs={view.runs} />
      </div>
    </section>
  );
}
