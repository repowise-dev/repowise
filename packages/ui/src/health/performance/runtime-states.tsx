"use client";

import type { EvidenceRunSummary } from "@repowise-dev/types/runtime-evidence";

import { formatPercent } from "../../lib/format";
import { CommandLine } from "../../shared/command-line";
import { AiPromptButton } from "../ai-prompt-button";
import { buildConnectEvidencePrompt } from "../ai-prompts/setup-prompt";
import { commitPosition, skippedText } from "./run-signal";
import type { RuntimeEvidenceView } from "./runtime";
import { Notice } from "./states";

/**
 * Why stored runtime evidence ranks nothing, or ranks less than it could.
 * Each notice answers a different question, and none of them says the code
 * is fast: a run measured only in tests, a newest run measured ahead of the
 * index, runs from an older commit, a view an update has not built yet, runs
 * that rank no production function, and runs whose frames mostly did not map.
 */

/** A commit pair a notice names: where the run was measured, and where the index is. */
interface Commits {
  measured: string;
  indexed: string;
}

type RuntimeNotice =
  /** `statements`: production statement windows are stored too; they do not rank functions. */
  | { kind: "tests_only"; workload: string; statements: boolean }
  | { kind: "ahead"; commits: Commits; showing: boolean; ranksNothing: boolean; command: string | null }
  | { kind: "stale"; command: string | null; older: Commits | null; changed: boolean }
  | { kind: "needs_update"; command: string | null }
  /** `placed`: the newest profile run is known to be at the indexed commit. */
  | { kind: "not_ranked"; command: string | null; placed: boolean }
  | { kind: "partial"; run: EvidenceRunSummary };

const sha7 = (sha: string) => sha.slice(0, 7);

/** A profile that can rank functions: a benchmark profile never does. */
const ranksFunctions = (r: { kind: string; class: string }) =>
  r.kind === "profile" && r.class !== "benchmark";

/**
 * Where the newest profile run sits against the index, when it is not at it.
 * Null until the runs are read, so a status notice it would correct waits.
 */
function newestProfile(view: RuntimeEvidenceView) {
  const runs = view.runs ?? [];
  const newest = runs.find(ranksFunctions);
  const indexed = view.referenceCommit;
  if (!newest?.commit_sha || !indexed) return null;
  const position = commitPosition(newest, runs, indexed);
  if (position !== "ahead" && position !== "older") return null;
  return { position, commits: { measured: newest.commit_sha, indexed } };
}

/** The notices a view carries, in reading order. Empty when there is nothing to say. */
export function runtimeNotices(view: RuntimeEvidenceView): RuntimeNotice[] {
  const out: RuntimeNotice[] = [];
  const status = view.overview?.status;
  const unranked = status === "stale" || status === "not_ranked" || status === "ahead_of_index";
  if (!view.run && view.testRun) {
    out.push({
      kind: "tests_only",
      workload: view.testRun.workload_name,
      statements: view.statementRuns.length > 0,
    });
  }
  // An unranked status is explained by the runs; say no more until they are read.
  if (unranked && view.runs === undefined) return out;
  const newest = newestProfile(view);
  if (newest?.position === "ahead") {
    // The newest run is ahead of the index, not short of samples: say that, never the
    // sample-count reason, which would be false for it.
    out.push({
      kind: "ahead",
      commits: newest.commits,
      showing: view.run?.commit === newest.commits.indexed,
      ranksNothing: unranked,
      command: unranked ? (view.overview?.command ?? null) : null,
    });
  } else if (status === "stale" || (status === "not_ranked" && newest?.position === "older")) {
    // An older newest run ranks nothing at this index for the same reason as a stale one:
    // say its commit, never the sample-count reason.
    out.push({
      kind: "stale",
      command: view.overview?.command ?? null,
      older: newest?.position === "older" ? newest.commits : null,
      changed: status === "stale",
    });
  }
  if (status === "needs_update") out.push({ kind: status, command: view.overview?.command ?? null });
  if (status === "not_ranked" && !newest) {
    const indexed = view.referenceCommit;
    const latest = view.runs?.find(ranksFunctions);
    out.push({
      kind: status,
      command: view.overview?.command ?? null,
      placed: Boolean(indexed && latest?.commit_sha === indexed),
    });
  }
  for (const run of view.runs ?? []) {
    // Only a profile's frames rank functions; other kinds map tests or lines.
    if (run.mapping_partial && run.kind === "profile") out.push({ kind: "partial", run });
  }
  return out;
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

function NoticeFor({
  notice,
  connectPrompt = buildConnectEvidencePrompt,
}: {
  notice: RuntimeNotice;
  connectPrompt?: (() => string) | undefined;
}) {
  switch (notice.kind) {
    case "tests_only":
      return (
        <Notice tone="neutral">
          <strong className="font-semibold text-[var(--color-text-primary)]">
            {notice.statements ? "Profiled in tests only." : "Measured in tests only."}
          </strong>{" "}
          The <code className="font-mono">{notice.workload}</code> profile orders functions within
          the test run
          {notice.statements ? ", and production statement windows do not rank functions" : ""}, so
          the queue keeps its code-shape order. Profile a benchmark or the CLI to rank by real work.
          <span className="mt-2 block">
            <AiPromptButton
              label="Copy prompt for your agent"
              copyText={() => connectPrompt()}
            />
          </span>
        </Notice>
      );
    case "ahead":
      return (
        <div>
          <Notice tone="neutral">
            <strong className="font-semibold text-[var(--color-text-primary)]">
              The newest run was measured at {sha7(notice.commits.measured)}, ahead of the indexed{" "}
              {sha7(notice.commits.indexed)}.
            </strong>{" "}
            Its functions rank once the index reaches that commit; update the index to rank them
            now.
            {notice.showing
              ? ` Showing the run at ${sha7(notice.commits.indexed)} until then.`
              : notice.ranksNothing
                ? " Until then nothing is ranked from it, which is not the same as nothing being slow."
                : ""}
          </Notice>
          {notice.command ? <CommandLine command={notice.command} /> : null}
        </div>
      );
    case "stale":
      return (
        <div>
          <Notice tone="warning">
            <strong className="font-semibold text-[var(--color-text-primary)]">
              {notice.older
                ? `Measured at ${sha7(notice.older.measured)}, older than the indexed ${sha7(notice.older.indexed)}.`
                : "Measured at another commit."}
            </strong>{" "}
            {notice.changed
              ? "Every function the stored runs measured has changed since, so nothing is ranked from them."
              : "Nothing is ranked from it at this index."}{" "}
            Run the workloads again
            {notice.older ? ` at ${sha7(notice.older.indexed)}` : ""} and add the results.
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
            {notice.placed
              ? "They have too few samples in repo code, or no frame in it, to rank one. Profile a workload that runs more repo code, or runs it for longer."
              : "They may be measured at a commit other than the indexed one, or have too few samples in repo code to rank one. The Runs list says which once it can be read."}
          </Notice>
          {notice.command ? <CommandLine command={notice.command} /> : null}
        </div>
      );
    case "partial":
      return <PartialNotice run={notice.run} />;
  }
}

export function RuntimeEvidenceNotices({
  notices,
  connectPrompt,
}: {
  notices: RuntimeNotice[];
  /** The text a notice that asks for more evidence copies. Omitted, the setup prompt. */
  connectPrompt?: (() => string) | undefined;
}) {
  if (notices.length === 0) return null;
  return (
    <div className="space-y-3">
      {notices.map((notice) => (
        <NoticeFor
          key={notice.kind === "partial" ? `partial:${notice.run.run_id}` : notice.kind}
          notice={notice}
          connectPrompt={connectPrompt}
        />
      ))}
    </div>
  );
}
