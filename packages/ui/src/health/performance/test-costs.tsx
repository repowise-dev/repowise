"use client";

import type {
  CostCluster,
  SlowTest,
  SuiteCost,
} from "@repowise-dev/types/runtime-evidence";

import { formatNumber } from "../../lib/format";
import { CollapsibleSection } from "../../shared/collapsible-section";
import { FilePath } from "../../shared/file-path";
import type { AiPromptRequest } from "../ai-prompt-action";
import {
  buildSlowTestsPrompt,
  type SlowTestsSubject,
} from "../ai-prompts/runtime-evidence-prompts";
import { RowPromptAction } from "../row-prompt-action";
import { plural } from "./measured-format";
import { QUIET_LINK_CLS } from "./queue";
import { secondsText, truncatedCount } from "./run-signal";
import { SLOW_TESTS_MORE, type TestsView } from "./runtime";
import { EvidenceWordTag } from "./tags";

/**
 * Stored test timings on the Performance tab: the groups of tests that run the
 * same code and take longer than the rest (excess clusters, with the fixture
 * they share) as ranked bars, the slowest tests with their file, a "Show 50",
 * and a plain line when the ingest cut the run at its row cap, since the lists
 * then miss whatever was cut. A suite under a minute, or with no group worth
 * a list, gets one line and no lists.
 */

const LIST =
  "divide-y divide-[var(--color-border-default)] border-y border-[var(--color-border-default)] text-xs";

/** A bar against the list's largest value; the figure beside it is its text. */
function Bar({ value, scale }: { value: number; scale: number }) {
  const pct = scale > 0 ? Math.max(1, Math.min(100, (value / scale) * 100)) : 0;
  return (
    <span
      className="relative block h-1 w-full rounded-full bg-[var(--color-bg-inset)]"
      aria-hidden="true"
    >
      <span
        className="absolute inset-y-0 left-0 rounded-full bg-[var(--color-text-secondary)]"
        style={{ width: `${pct}%` }}
      />
    </span>
  );
}

function ClusterRow({
  cluster,
  scale,
  fileHref,
  onPrompt,
}: {
  cluster: CostCluster;
  scale: number;
  fileHref: (path: string) => string;
  onPrompt: () => void;
}) {
  const more = cluster.files_total - cluster.files.length;
  const fx = cluster.fixture;
  return (
    <li className="group/row grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1.5 px-1 py-2.5">
      <span className="min-w-0 text-[var(--color-text-primary)]">
        {plural(cluster.tests, "test")} at {secondsText(cluster.mean_s)} each
        vs {secondsText(cluster.others_mean_s)}
      </span>
      <span className="shrink-0 font-mono tabular-nums text-[var(--color-text-secondary)]">
        +{secondsText(cluster.excess_s)}
      </span>
      <Bar value={cluster.excess_s} scale={scale} />
      <span className="flex items-center justify-end gap-2">
        {cluster.share != null ? (
          <span className="font-mono text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
            {Math.round(cluster.share * 100)}% of the suite
          </span>
        ) : null}
        <RowPromptAction
          variant="icon"
          onClick={onPrompt}
          subject={`the ${plural(cluster.tests, "test")} that run ${cluster.files[0] ?? "the same code"}`}
        />
      </span>
      <span className="col-span-2 flex min-w-0 flex-col gap-0.5 text-[11px]">
        {cluster.files.map((path) => (
          <a
            key={path}
            href={fileHref(path)}
            className={`flex min-w-0 font-mono ${QUIET_LINK_CLS}`}
          >
            <FilePath path={path} fold className="text-[11px]" />
          </a>
        ))}
        {more > 0 ? (
          <span className="text-[var(--color-text-tertiary)]">
            and {plural(more, "more file")}
          </span>
        ) : null}
        {cluster.within ? (
          <span className="text-[var(--color-text-tertiary)]">
            Inside the {formatNumber(cluster.within.tests)} tests that run{" "}
            <span className="font-mono">{cluster.within.files[0]}</span>
          </span>
        ) : null}
        {fx ? (
          <span className="text-[var(--color-text-secondary)]">
            Fixture <span className="font-mono">{fx.name}</span>{" "}
            <a
              href={fileHref(fx.file_path)}
              className={`font-mono ${QUIET_LINK_CLS}`}
            >
              {fx.file_path}
              {fx.line != null ? `:${fx.line}` : ""}
            </a>
            , asked for by {formatNumber(fx.tests)} of them and{" "}
            {fx.others === 0
              ? "no other timed test"
              : plural(fx.others, "other timed test")}
          </span>
        ) : null}
      </span>
    </li>
  );
}

function TestRow({
  test,
  fileHref,
  onPrompt,
}: {
  test: SlowTest;
  fileHref: (path: string) => string;
  onPrompt: () => void;
}) {
  return (
    <li className="group/row flex items-baseline gap-3 px-1 py-2.5">
      <span className="min-w-0 flex-1">
        <span className="block font-mono text-[var(--color-text-primary)] [overflow-wrap:anywhere]">
          {test.test_id}
        </span>
        {test.test_file ? (
          <a
            href={fileHref(test.test_file)}
            className={`mt-0.5 flex min-w-0 font-mono text-[11px] ${QUIET_LINK_CLS}`}
          >
            <FilePath path={test.test_file} fold className="text-[11px]" />
          </a>
        ) : null}
      </span>
      {test.outcome && test.outcome !== "passed" ? (
        <EvidenceWordTag>{test.outcome}</EvidenceWordTag>
      ) : null}
      <span className="shrink-0 font-mono tabular-nums text-[var(--color-text-secondary)]">
        {secondsText(test.duration_s)}
      </span>
      <RowPromptAction
        variant="icon"
        onClick={onPrompt}
        subject={test.test_id}
      />
    </li>
  );
}

const subhead = "mb-2 text-xs font-medium text-[var(--color-text-secondary)]";

/** The suite in one line: what its time adds up to and how concentrated it is. */
export function costLine(cost: SuiteCost): string {
  const total = secondsText(cost.seconds);
  const timed = plural(cost.timed, "timed test");
  if (cost.verdict === "small_suite") {
    return `Tests take ${total} in all (${timed}): under a minute, nothing to speed up.`;
  }
  if (cost.verdict === "small_excess") {
    return `Tests take ${total} (${timed}), spread evenly: no group of tests runs 5 s over the rest.`;
  }
  const top = cost.concentration;
  const spread = top
    ? `; the slowest ${formatNumber(top.tests)} take ${Math.round(top.share * 100)}% of it`
    : "";
  return `Tests take ${total} (${timed})${spread}.`;
}

/** "30,000 of 49,132 tests timed; 19,104 cut ...", or null when nothing was cut. */
export function cutLine(tests: TestsView): string | null {
  const run = tests.suite.runs.find(
    (r) => r.kind === "test_timing" && truncatedCount(r) > 0,
  );
  if (!run) return null;
  const cut = truncatedCount(run);
  const { matched, total } = run.units;
  const timed =
    matched != null
      ? `${formatNumber(matched)}${total != null ? ` of ${formatNumber(total)}` : ""} tests`
      : "Tests";
  return `${timed} timed in ${run.workload}: ${formatNumber(cut)} were cut at the stored row cap in file order, not by duration, so a slow test past the cut is missing here and file totals undercount.`;
}

export function TestsGroup({
  tests,
  fileHref,
  onPrompt,
}: {
  tests: TestsView | null;
  fileHref: (path: string) => string;
  onPrompt: (request: AiPromptRequest) => void;
}) {
  if (!tests) return null;
  const { suite, limit, more, loading, failed } = tests;
  const cost = suite.cost;
  // Silence: a suite under a minute, or with no group of tests worth a list.
  const quiet =
    cost?.verdict === "small_suite" || cost?.verdict === "small_excess";
  const clusters = cost?.clusters ?? [];
  const scale = Math.max(0, ...clusters.map((c) => c.excess_s));
  const cut = quiet ? null : cutLine(tests);
  const ask = (
    subject: SlowTestsSubject,
    title: string,
    filePath: string | null,
  ) =>
    onPrompt({
      title: "Prompt for your agent",
      description: title,
      filePath,
      getPrompt: (flavor) => buildSlowTestsPrompt({ subject, flavor }),
    });
  const caption = [
    `${plural(suite.tests_total, "timed test")}`,
    cost ? secondsText(cost.seconds) : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const fewer = suite.slow_tests.length < limit;
  return (
    <CollapsibleSection
      title={
        <>
          <span className="text-[15px] font-semibold text-[var(--color-text-primary)]">
            Test time
          </span>
          <span className="block text-xs font-normal text-[var(--color-text-tertiary)]">
            {caption}
          </span>
        </>
      }
      hint={cut ? "cut at the row cap" : undefined}
    >
      <div className="space-y-4 pb-4">
        {cut ? (
          <p role="note" className="text-xs text-[var(--color-text-secondary)]">
            {cut}
          </p>
        ) : null}
        {cost ? (
          <p className="text-xs text-[var(--color-text-secondary)]">
            {costLine(cost)}
          </p>
        ) : null}
        {cost?.verdict === "no_test_map" && cost.command ? (
          <p className="text-xs text-[var(--color-text-tertiary)]">
            To see which code makes them slow, record a per-test coverage map:{" "}
            <code className="font-mono">{cost.command}</code>, then{" "}
            <code className="font-mono">repowise coverage add .coverage</code>.
          </p>
        ) : null}
        {quiet ? null : (
          <div className="grid gap-x-8 gap-y-6 lg:grid-cols-2">
            {clusters.length > 0 ? (
              <div className="min-w-0">
                <h4 className={subhead}>Groups of tests slower than the rest</h4>
                <ul aria-label="Groups of slow tests" className={LIST}>
                  {clusters.map((cluster) => (
                    <ClusterRow
                      key={cluster.files.join("|")}
                      cluster={cluster}
                      scale={scale}
                      fileHref={fileHref}
                      onPrompt={() =>
                        ask(
                          { kind: "cluster", cluster },
                          `The ${plural(cluster.tests, "test")} that run the same code, faster without losing coverage.`,
                          cluster.fixture?.file_path ?? cluster.files[0] ?? null,
                        )
                      }
                    />
                  ))}
                </ul>
                <p className="mt-2 text-xs text-[var(--color-text-tertiary)]">
                  Each group&apos;s time over the other tests&apos; pace; which
                  tests run which code comes from the per-test coverage map.
                </p>
              </div>
            ) : null}
            {suite.slow_tests.length > 0 ? (
              <div className="min-w-0">
                <h4 className={subhead}>Slowest tests</h4>
                <ul aria-label="Slowest tests" className={LIST}>
                  {suite.slow_tests.map((test) => (
                    <TestRow
                      key={`${test.workload}:${test.test_id}`}
                      test={test}
                      fileHref={fileHref}
                      onPrompt={() =>
                        ask(
                          { kind: "tests", tests: [test] },
                          `${test.test_id}, faster without losing coverage.`,
                          test.test_file,
                        )
                      }
                    />
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        )}
        {failed ? (
          <p
            role="status"
            className="text-xs text-[var(--color-text-tertiary)]"
          >
            Could not read more rows. Reload to try again.
          </p>
        ) : loading ? (
          <p
            role="status"
            className="text-xs text-[var(--color-text-tertiary)]"
          >
            Reading more rows.
          </p>
        ) : more && !fewer && !quiet ? (
          <button
            type="button"
            onClick={more}
            className={`text-xs ${QUIET_LINK_CLS}`}
          >
            Show {formatNumber(SLOW_TESTS_MORE)}
          </button>
        ) : null}
      </div>
    </CollapsibleSection>
  );
}
