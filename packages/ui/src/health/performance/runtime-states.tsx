"use client";

import type { EvidenceRunSummary } from "@repowise-dev/types/runtime-evidence";

import { formatNumber, formatPercent } from "../../lib/format";
import { CommandLine } from "../../shared/command-line";
import { AiPromptButton } from "../ai-prompt-button";
import { buildConnectEvidencePrompt } from "../ai-prompts/setup-prompt";
import { humanizeToken } from "./presentation";
import type { RuntimeEvidenceView } from "./runtime";
import { Notice } from "./states";

/**
 * Why stored runtime evidence ranks nothing, or ranks less than it could.
 * Each notice answers a different question, and none of them says the code
 * is fast: a run measured only in tests, runs from another commit, a view an
 * update has not built yet, runs that rank no production function, and runs
 * whose frames mostly did not map.
 */

type RuntimeNotice =
  | { kind: "tests_only"; workload: string }
  | { kind: "stale" | "needs_update" | "not_ranked"; command: string | null }
  | { kind: "partial"; run: EvidenceRunSummary };

/** The notices a view carries, in reading order. Empty when there is nothing to say. */
export function runtimeNotices(view: RuntimeEvidenceView): RuntimeNotice[] {
  const out: RuntimeNotice[] = [];
  const status = view.overview?.status;
  if (!view.run && view.testRun) out.push({ kind: "tests_only", workload: view.testRun.workload_name });
  if (status === "stale" || status === "needs_update" || status === "not_ranked") {
    out.push({ kind: status, command: view.overview?.command ?? null });
  }
  for (const run of view.runs ?? []) {
    // Only a profile's frames rank functions; other kinds map tests or lines.
    if (run.mapping_partial && run.kind === "profile") out.push({ kind: "partial", run });
  }
  return out;
}

function skippedText(skipped: Record<string, number> | null): string | null {
  const entries = Object.entries(skipped ?? {}).filter(([, n]) => n > 0);
  if (entries.length === 0) return null;
  return entries
    .map(([reason, n]) => `${formatNumber(n)} ${humanizeToken(reason).toLowerCase()}`)
    .join(", ");
}

function PartialNotice({ run }: { run: EvidenceRunSummary }) {
  const { matched, total } = run.units;
  const share = matched != null && total ? formatPercent(matched / total) : null;
  const skipped = skippedText(run.skipped);
  return (
    <Notice tone="warning">
      <strong className="font-semibold text-[var(--color-text-primary)]">
        {share ? `${share} of entries in ` : "Some entries in "}
        <code className="font-mono">{run.workload}</code> matched indexed code
      </strong>
      , so the run is stored but not used for ranking.
      {skipped ? ` Skipped: ${skipped}.` : ""} Usually the run came from another checkout or a
      tree with uncommitted changes.
    </Notice>
  );
}

function NoticeFor({ notice }: { notice: RuntimeNotice }) {
  switch (notice.kind) {
    case "tests_only":
      return (
        <Notice tone="neutral">
          <strong className="font-semibold text-[var(--color-text-primary)]">
            Measured in tests only.
          </strong>{" "}
          The <code className="font-mono">{notice.workload}</code> profile orders functions within
          the test run, so the queue keeps its code-shape order. Profile a benchmark or the CLI to
          rank by real work.
          <span className="mt-2 block">
            <AiPromptButton
              label="Copy prompt for your agent"
              copyText={() => buildConnectEvidencePrompt()}
            />
          </span>
        </Notice>
      );
    case "stale":
      return (
        <div>
          <Notice tone="warning">
            <strong className="font-semibold text-[var(--color-text-primary)]">
              Measured at another commit.
            </strong>{" "}
            Every function the stored runs measured has changed since, so nothing is ranked from
            them. Run the workloads again and add the results.
          </Notice>
          {notice.command ? <CommandLine command={notice.command} /> : null}
        </div>
      );
    case "needs_update":
      return (
        <div>
          <Notice tone="neutral">
            <strong className="font-semibold text-[var(--color-text-primary)]">
              Runs are stored, and the per-function view is built on the next update.
            </strong>{" "}
            Until then nothing is ranked, which is not the same as nothing being slow.
          </Notice>
          {notice.command ? <CommandLine command={notice.command} /> : null}
        </div>
      );
    case "not_ranked":
      return (
        <div>
          <Notice tone="neutral">
            <strong className="font-semibold text-[var(--color-text-primary)]">
              The stored runs rank no production function.
            </strong>{" "}
            They have too few samples in repo code, or no frame in it, to rank one. Profile a
            workload that runs more repo code, or runs it for longer.
          </Notice>
          {notice.command ? <CommandLine command={notice.command} /> : null}
        </div>
      );
    case "partial":
      return <PartialNotice run={notice.run} />;
  }
}

export function RuntimeEvidenceNotices({ notices }: { notices: RuntimeNotice[] }) {
  if (notices.length === 0) return null;
  return (
    <div className="space-y-3">
      {notices.map((notice) => (
        <NoticeFor
          key={notice.kind === "partial" ? `partial:${notice.run.run_id}` : notice.kind}
          notice={notice}
        />
      ))}
    </div>
  );
}
