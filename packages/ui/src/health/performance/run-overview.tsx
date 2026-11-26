"use client";

import type {
  EvidenceRunSummary,
  MeasuredRun,
} from "@repowise-dev/types/runtime-evidence";

import { formatPercent } from "../../lib/format";
import { ProportionBar } from "../../shared/proportion-bar";
import { AiPromptButton } from "../ai-prompt-button";
import { buildRichProfilePrompt } from "../ai-prompts/runtime-evidence-prompts";
import { MICRO } from "../tokens";
import {
  changeMark,
  changeText,
  commitPosition,
  lowSignalCauses,
  lowSignalText,
  runHistory,
  runSeconds,
  runShares,
  secondsText,
  type LowSignalCause,
  type RunShares,
} from "./run-signal";
import { Notice } from "./states";
import { EvidenceWordTag } from "./tags";

/**
 * The open profile run in one block: its total and change from the previous
 * run of the workload, where its time went, and whether it carries enough
 * signal to rank on. Read from the run's header, so it shows even when the
 * run ranks nothing.
 */

/** What a host hands an agent to collect a profile that ranks; omitted, the built-in prompt. */
export type LowSignalPromptBuilder = (signal: {
  workload: string;
  tool: string | null;
  causes: readonly LowSignalCause[];
}) => string;

/** Neutral ink for a run's split: green, amber and red stay with health. */
const SPLIT_INK = {
  repo: "var(--color-text-primary)",
  library:
    "color-mix(in srgb, var(--color-text-tertiary) 55%, var(--color-bg-inset))",
  wait: "color-mix(in srgb, var(--color-text-tertiary) 28%, var(--color-bg-inset))",
  unattributed:
    "color-mix(in srgb, var(--color-text-tertiary) 12%, var(--color-bg-inset))",
} as const;

const SPLIT_LABEL = {
  repo: "Repo code",
  library: "Libraries",
  wait: "Waiting",
  unattributed: "Unattributed",
} as const;

/**
 * The split as a bar. With `legend`, each part is named with its share (and
 * its seconds when the run's total is a time), which is the bar's text
 * equivalent; without it the bar carries the same words as its label.
 */
export function SplitBar({
  shares,
  label,
  seconds = null,
  legend = false,
}: {
  shares: RunShares;
  label: string;
  /** The run's total in seconds, to name each part's time too. */
  seconds?: number | null;
  legend?: boolean;
}) {
  const segments = (
    Object.keys(SPLIT_LABEL) as (keyof typeof SPLIT_LABEL)[]
  ).flatMap((key) => {
    const value = shares[key];
    if (value == null || value <= 0) return [];
    const detail =
      seconds != null
        ? `${formatPercent(value)} · ${secondsText(value * seconds)}`
        : formatPercent(value, 1);
    return [
      { key, label: SPLIT_LABEL[key], value, detail, color: SPLIT_INK[key] },
    ];
  });
  if (segments.length === 0) return null;
  return (
    <ProportionBar
      segments={segments}
      sort={false}
      size="sm"
      legend={legend}
      label={label}
    />
  );
}

function positionText(
  run: EvidenceRunSummary,
  runs: readonly EvidenceRunSummary[],
  reference: string | null,
) {
  const at = run.commit_sha?.slice(0, 7);
  if (!at) return null;
  switch (commitPosition(run, runs, reference)) {
    case "indexed":
      return `at ${at}, the indexed commit`;
    case "ahead":
      return `at ${at}, ahead of the index`;
    case "older":
      return `at ${at}, older than the index`;
    default:
      return `at ${at}`;
  }
}

function LowSignalNotice({
  run,
  causes,
  prompt,
}: {
  run: EvidenceRunSummary;
  causes: readonly LowSignalCause[];
  prompt: LowSignalPromptBuilder;
}) {
  return (
    <Notice tone="neutral">
      <strong className="font-semibold text-[var(--color-text-primary)]">
        Low signal.
      </strong>{" "}
      This run has {causes.map(lowSignalText).join("; ")}. What it ranks is a
      rough order, not a measurement to steer by.
      <span className="mt-2 block">
        <AiPromptButton
          label="Copy prompt: get a profile that ranks"
          copyText={() =>
            prompt({ workload: run.workload, tool: run.tool, causes })
          }
        />
      </span>
    </Notice>
  );
}

/**
 * `summary` is the open run's header; `measured` its ranked basis when it
 * ranks. `heading` shows the block's own title and names the run, for when
 * no strip sits above to name it.
 */
export function RunOverview({
  summary,
  measured,
  runs,
  reference,
  heading = false,
  lowSignalPrompt = buildRichProfilePrompt,
}: {
  summary: EvidenceRunSummary;
  measured: MeasuredRun | null;
  runs: readonly EvidenceRunSummary[];
  reference: string | null;
  heading?: boolean;
  lowSignalPrompt?: LowSignalPromptBuilder | undefined;
}) {
  const seconds = runSeconds(summary);
  const change = runHistory(runs).changes.get(summary.run_id);
  const mark = change ? changeMark(change) : null;
  const shares = runShares(summary);
  const causes = lowSignalCauses(summary, measured);
  const total = seconds != null ? secondsText(seconds) : null;
  const where = positionText(summary, runs, reference);
  return (
    <div className="space-y-3" data-run-overview={summary.run_id}>
      {heading ? <p className={MICRO}>Runtime evidence</p> : null}
      <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm text-[var(--color-text-secondary)]">
        {heading ? (
          <code className="font-mono text-[var(--color-text-primary)]">
            {summary.workload}
          </code>
        ) : null}
        {total ? (
          <span className="font-mono tabular-nums text-[var(--color-text-primary)]">
            {total}
            {summary.value_kind === "wall" ? " wall" : ""}
          </span>
        ) : null}
        {change ? (
          <span className="font-mono text-xs tabular-nums">
            {changeText(change, summary.unit)} vs the previous run
          </span>
        ) : null}
        {mark ? (
          <EvidenceWordTag ink={mark === "slower"}>{mark}</EvidenceWordTag>
        ) : null}
        {heading && where ? (
          <span className="text-xs text-[var(--color-text-tertiary)]">
            {where}
          </span>
        ) : null}
      </p>
      {shares ? (
        <div className="max-w-2xl">
          <SplitBar
            shares={shares}
            seconds={seconds}
            legend
            label={`Time split of the ${summary.workload} run`}
          />
        </div>
      ) : null}
      {causes.length > 0 ? (
        <LowSignalNotice
          run={summary}
          causes={causes}
          prompt={lowSignalPrompt}
        />
      ) : null}
    </div>
  );
}

/**
 * The run the block describes: the open workload's when one ranks, else the
 * newest profile at the indexed commit, else the newest profile.
 */
export function overviewRun(
  runs: readonly EvidenceRunSummary[] | undefined,
  open: MeasuredRun | null,
  reference: string | null,
): EvidenceRunSummary | null {
  if (!runs) return null;
  if (open) return runs.find((r) => r.run_id === open.run_id) ?? null;
  // A unit-test profile never leads; it is listed apart.
  const profiles = runs.filter(
    (r) => r.kind === "profile" && r.class !== "unit_test",
  );
  return (
    profiles.find((r) => reference && r.commit_sha === reference) ??
    profiles[0] ??
    null
  );
}
