"use client";

import type {
  FileTestCost,
  SlowTest,
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
 * Stored test timings on the Performance tab: the test time per production
 * file as ranked bars, the slowest tests with their file, a "Show 50" for
 * both, and a plain line when the ingest cut the run at its row cap, since
 * the lists then miss whatever was cut.
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

function FileRow({
  file,
  scale,
  href,
  onPrompt,
}: {
  file: FileTestCost;
  scale: number;
  href: string;
  onPrompt: () => void;
}) {
  return (
    <li className="group/row grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1.5 px-1 py-2.5">
      <a href={href} className={`flex min-w-0 font-mono ${QUIET_LINK_CLS}`}>
        <FilePath path={file.file_path} fold className="text-xs" />
      </a>
      <span className="shrink-0 font-mono tabular-nums text-[var(--color-text-secondary)]">
        {secondsText(file.test_seconds)}
      </span>
      <Bar value={file.test_seconds} scale={scale} />
      <span className="flex items-center justify-end gap-2">
        <span className="font-mono text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
          {plural(file.timed_tests, "timed test")}
        </span>
        <RowPromptAction
          variant="icon"
          onClick={onPrompt}
          subject={`tests that run ${file.file_path}`}
        />
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

function fileNote(
  costs: NonNullable<TestsView["suite"]["file_costs"]>,
): string {
  return [
    costs.basis === "inferred"
      ? "Which tests run a file is inferred from the call graph."
      : "Which tests run a file comes from the per-test coverage map.",
    costs.every_test > 0
      ? `${plural(costs.every_test, "file")} every timed test runs ${costs.every_test === 1 ? "is" : "are"} counted, not listed.`
      : null,
    costs.not_production > 0
      ? `${plural(costs.not_production, "test or tooling file")} counted, not listed.`
      : null,
  ]
    .filter(Boolean)
    .join(" ");
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
  const files = suite.file_costs?.files ?? [];
  const scale = Math.max(0, ...files.map((f) => f.test_seconds));
  const cut = cutLine(tests);
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
    suite.file_costs
      ? plural(suite.file_costs.files_total, "production file")
      : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const fewer = files.length < limit && suite.slow_tests.length < limit;
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
        <div className="grid gap-x-8 gap-y-6 lg:grid-cols-2">
          {files.length > 0 ? (
            <div className="min-w-0">
              <h4 className={subhead}>By production file, test seconds</h4>
              <ul aria-label="Test time by production file" className={LIST}>
                {files.map((file) => (
                  <FileRow
                    key={file.file_path}
                    file={file}
                    scale={scale}
                    href={fileHref(file.file_path)}
                    onPrompt={() =>
                      ask(
                        { kind: "file", file, basis: suite.file_costs!.basis },
                        `The tests that run ${file.file_path}, faster without losing coverage.`,
                        file.file_path,
                      )
                    }
                  />
                ))}
              </ul>
              <p className="mt-2 text-xs text-[var(--color-text-tertiary)]">
                {fileNote(suite.file_costs!)}
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
        ) : more && !fewer ? (
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
