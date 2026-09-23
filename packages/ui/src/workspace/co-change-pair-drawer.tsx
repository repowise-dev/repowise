"use client";

import * as React from "react";
import { ExternalLink } from "lucide-react";
import { AdaptivePanel } from "../shared/adaptive-panel";
import { InfoTip } from "../shared/info-tip";
import { formatDate, formatDateTime } from "../lib/format";
import { PAIR_MICRO as MICRO, PairAgentSection, PairFactGrid, type PairFact } from "../coupling/pair-drawer-parts";
import { RepoPath } from "./co-change-table";
import {
  gapPhrase,
  hiddenCouplingSentence,
  MAX_COMMIT_PAIRS,
  SESSION_WINDOW_HOURS,
  SESSIONS_DEFINITION,
  STRENGTH_DEFINITION,
  type CoChangeCommit,
  type CoChangeLink,
  type CoChangeStructureFacts,
  type NearbyCommitPair,
} from "./co-change-facts";
import type { WorkspaceCoChangeEntry } from "@repowise-dev/types/workspace";

/** Injected link component (e.g. Next's Link); defaults to a plain anchor. */
type LinkLike = React.ElementType<{ href: string; className?: string; children: React.ReactNode }>;

/** Evidence fetched when the drawer opens: pending, missing (with why), or here. */
export type Loadable<T> =
  | { state: "loading" }
  | { state: "unavailable"; reason: string }
  | { state: "ready"; data: T };

export interface CoChangeFileHistory {
  commits90d: number;
  churnPercentile: number;
  priorFixes: number;
  isHotspot: boolean;
  /** The per-repo index's most recent significant commits for this file. */
  commits: CoChangeCommit[];
}

export interface CoChangePairDrawerProps {
  /** The pair to describe; `null` closes the panel. */
  pair: WorkspaceCoChangeEntry | null;
  onClose: () => void;
  structure: Loadable<CoChangeStructureFacts>;
  sourceHistory: Loadable<CoChangeFileHistory>;
  targetHistory: Loadable<CoChangeFileHistory>;
  /** Commits reconstructed from both histories; null until both are ready. The
   *  host computes them once and hands the same list to the agent prompt. */
  commits: NearbyCommitPair[] | null;
  /** A file's page in its own repository; null when that repo is not indexed. */
  fileHref?: ((repo: string, path: string) => string | null) | undefined;
  /** Where one contract link opens. */
  contractHref?: ((link: CoChangeLink) => string) | undefined;
  LinkComponent?: LinkLike | undefined;
  onGeneratePrompt: () => void;
}

function basename(path: string): string {
  return path.split("/").pop() ?? path;
}

/**
 * Everything the workspace knows about one cross-repo file pair: the claim,
 * whether anything declared explains it, both files with their history, the
 * commits that plausibly formed the shared sessions, and the agent prompt.
 *
 * Same anatomy as the single-repo `CouplingPairDrawer` (verdict and claim,
 * hairline facts, sections under micro-headings, one action) through the
 * shared pair parts. The verdict here is structural: a contract joining the
 * two files explains the co-change; its absence is the exception worth a mark.
 */
export function CoChangePairDrawer({
  pair,
  onClose,
  structure,
  sourceHistory,
  targetHistory,
  commits,
  fileHref,
  contractHref,
  LinkComponent,
  onGeneratePrompt,
}: CoChangePairDrawerProps) {
  const Anchor: LinkLike = LinkComponent ?? "a";

  const linked = structure.state === "ready" && structure.data.pairLinks.length > 0;
  const hidden = structure.state === "ready" && structure.data.pairLinks.length === 0;

  const facts: PairFact[] = pair
    ? [
        {
          label: "Strength",
          hint: <InfoTip content={STRENGTH_DEFINITION} label="What strength means" />,
          value: (
            <span className="font-mono text-lg font-semibold tabular-nums text-[var(--color-text-primary)]">
              {Math.round(pair.strength * 100)}%
            </span>
          ),
        },
        {
          label: "Shared sessions",
          hint: <InfoTip content={SESSIONS_DEFINITION} label="What a shared session is" />,
          value: (
            <span className="text-xs text-[var(--color-text-primary)]">
              <span className="font-mono text-lg font-semibold tabular-nums">{pair.frequency}</span>{" "}
              <span className="text-[var(--color-text-tertiary)]">work sessions</span>
            </span>
          ),
        },
        {
          label: "Last together",
          value: (
            <span
              className="font-mono text-xs tabular-nums text-[var(--color-text-primary)]"
              title={pair.last_date ? formatDateTime(pair.last_date) : undefined}
            >
              {pair.last_date ? formatDate(pair.last_date) : "unknown"}
            </span>
          ),
        },
        {
          label: "Contract links",
          value: (
            <span className="text-xs text-[var(--color-text-primary)]">
              {structure.state === "loading" ? (
                <span className="text-[var(--color-text-tertiary)]">checking</span>
              ) : structure.state === "unavailable" ? (
                <span className="text-[var(--color-text-tertiary)]">not checked</span>
              ) : linked ? (
                <span className="font-mono tabular-nums">{structure.data.pairLinks.length}</span>
              ) : (
                "none between these files"
              )}
            </span>
          ),
        },
      ]
    : [];

  return (
    <AdaptivePanel
      open={pair !== null}
      onOpenChange={(o) => !o && onClose()}
      modal={false}
      widthClassName="md:max-w-[640px]"
      eyebrow="Cross-repo co-change"
      title={
        pair ? (
          <span className="font-mono [overflow-wrap:break-word]">
            {basename(pair.source_file)} and {basename(pair.target_file)}
          </span>
        ) : (
          ""
        )
      }
    >
      {pair && (
        <div className="flex flex-col gap-6 px-4 py-4">
          <div className="flex flex-col gap-2">
            <p className="flex items-center gap-1.5">
              <span
                className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                  hidden ? "bg-[var(--color-caution)]" : "bg-[var(--color-text-tertiary)]"
                }`}
                aria-hidden
              />
              <span className={MICRO}>
                {structure.state === "loading"
                  ? "Checking declared links"
                  : structure.state === "unavailable"
                    ? "Declared links not checked"
                    : linked
                      ? "Linked by contract"
                      : "No declared link"}
              </span>
            </p>
            <p className="text-[15px] leading-relaxed text-[var(--color-text-primary)] [text-wrap:pretty]">
              <span className="font-mono">{basename(pair.source_file)}</span> in {pair.source_repo} and{" "}
              <span className="font-mono">{basename(pair.target_file)}</span> in {pair.target_repo} changed in
              the same work session {pair.frequency === 1 ? "once" : `${pair.frequency} times`}
              {pair.last_date ? `, most recently on ${formatDate(pair.last_date)}` : ""}.
            </p>
            {structure.state === "ready" && (
              <p className="text-xs leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
                {linked
                  ? "A contract joins these files, so at least part of the co-change is accounted for. Worth a look only if the contract is the wrong shape or one side restates it by hand."
                  : `${hiddenCouplingSentence(pair, structure.data)} Until that changes, check the other file whenever you edit one.`}
              </p>
            )}
            {structure.state === "unavailable" && (
              <p className="text-xs leading-relaxed text-[var(--color-text-tertiary)]">{structure.reason}</p>
            )}
          </div>

          <PairFactGrid facts={facts} />

          <section className="flex flex-col gap-2">
            <h3 className={MICRO}>The two files</h3>
            <ul className="border-y border-[var(--color-border-default)]">
              {(
                [
                  [pair.source_repo, pair.source_file, sourceHistory],
                  [pair.target_repo, pair.target_file, targetHistory],
                ] as const
              ).map(([repo, path, history]) => {
                const href = fileHref?.(repo, path) ?? null;
                return (
                  <li
                    key={`${repo}:${path}`}
                    className="min-w-0 border-t border-[var(--color-border-default)] px-3 py-2.5 first:border-t-0"
                  >
                    {/* Wrapped, not truncated: the one place the exact file is legible. */}
                    <RepoPath repo={repo} path={path} wrap />
                    <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--color-text-tertiary)]">
                      <FileHistoryFacts history={history} />
                      {href ? (
                        <Anchor
                          href={href}
                          className="inline-flex items-center gap-1 font-medium text-[var(--color-accent-primary)] hover:underline"
                        >
                          <ExternalLink className="h-3 w-3" aria-hidden />
                          Open file page
                        </Anchor>
                      ) : (
                        <span>{repo} is not indexed, so there is no file page</span>
                      )}
                    </p>
                  </li>
                );
              })}
            </ul>
          </section>

          <section className="flex flex-col gap-2">
            <h3 className={MICRO}>Commits close together</h3>
            <CommitEvidence
              pair={pair}
              commits={commits}
              sourceHistory={sourceHistory}
              targetHistory={targetHistory}
            />
          </section>

          {linked && (
            <section className="flex flex-col gap-2">
              <h3 className={MICRO}>Contracts joining them</h3>
              <ul className="border-y border-[var(--color-border-default)]">
                {structure.data.pairLinks.map((l) => (
                  <li
                    key={`${l.contract_id}|${l.provider_file}|${l.consumer_file}`}
                    className="min-w-0 border-t border-[var(--color-border-default)] px-3 py-2.5 first:border-t-0"
                  >
                    <p className="break-all font-mono text-xs text-[var(--color-text-primary)]">{l.contract_id}</p>
                    <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--color-text-tertiary)]">
                      <span>
                        {l.contract_type}: {l.provider_repo} provides, {l.consumer_repo} consumes
                      </span>
                      {contractHref && (
                        <Anchor
                          href={contractHref(l)}
                          className="font-medium text-[var(--color-accent-primary)] hover:underline"
                        >
                          Open contract
                        </Anchor>
                      )}
                    </p>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <PairAgentSection
            body="Builds a prompt carrying the evidence above, so your agent can find the concept these files share, decide whether a contract, shared type or generated client should replace the manual lockstep, and keep the other file in view when editing one."
            label="AI investigation prompt"
            onClick={onGeneratePrompt}
          />
        </div>
      )}
    </AdaptivePanel>
  );
}

function FileHistoryFacts({ history }: { history: Loadable<CoChangeFileHistory> }) {
  if (history.state === "loading") return <span>Loading history</span>;
  if (history.state === "unavailable") return <span>{history.reason}</span>;
  const h = history.data;
  return (
    <>
      <span className="tabular-nums">
        {h.commits90d} {h.commits90d === 1 ? "commit" : "commits"} in 90 days
      </span>
      <span className="tabular-nums">churn percentile {Math.round(h.churnPercentile)}</span>
      {h.priorFixes > 0 && (
        <span className="tabular-nums">
          {h.priorFixes} prior {h.priorFixes === 1 ? "fix" : "fixes"}
        </span>
      )}
      {/* The exception gets the mark; an ordinary file says nothing. */}
      {h.isHotspot && (
        <span className="inline-flex items-center gap-1 text-[var(--color-text-secondary)]">
          <span className="h-1.5 w-1.5 rounded-full bg-[var(--color-warning)]" aria-hidden />
          Hotspot
        </span>
      )}
    </>
  );
}

function CommitEvidence({
  pair,
  commits,
  sourceHistory,
  targetHistory,
}: {
  pair: WorkspaceCoChangeEntry;
  commits: NearbyCommitPair[] | null;
  sourceHistory: Loadable<CoChangeFileHistory>;
  targetHistory: Loadable<CoChangeFileHistory>;
}) {
  const caveat = "The miner counts shared sessions but does not record which commits formed them.";
  if (sourceHistory.state === "loading" || targetHistory.state === "loading") {
    return <p className="text-xs text-[var(--color-text-tertiary)]">Loading each file&apos;s history.</p>;
  }
  if (commits === null) {
    return (
      <p className="text-xs leading-relaxed text-[var(--color-text-tertiary)]">
        {caveat} Commit history for one of the files is not available here, so they cannot be
        reconstructed.
      </p>
    );
  }
  const nA = sourceHistory.state === "ready" ? sourceHistory.data.commits.length : 0;
  const nB = targetHistory.state === "ready" ? targetHistory.data.commits.length : 0;
  const basis = `Matched by author within ${SESSION_WINDOW_HOURS} hours, from the ${nA} and ${nB} most recent significant commits of each file. ${caveat}`;
  if (commits.length === 0) {
    return (
      <p className="text-xs leading-relaxed text-[var(--color-text-tertiary)]">
        None of the recent significant commits line up. {basis}
      </p>
    );
  }
  const shown = commits.slice(0, MAX_COMMIT_PAIRS);
  return (
    <>
      <ul className="border-y border-[var(--color-border-default)]">
        {shown.map((c) => (
          <li
            key={`${c.source.sha}|${c.target.sha}`}
            className="min-w-0 border-t border-[var(--color-border-default)] px-3 py-2.5 first:border-t-0"
          >
            <p className="font-mono text-[10px] tabular-nums text-[var(--color-text-tertiary)]">
              {formatDate(c.source.date)}
              {c.source.author ? ` · ${c.source.author}` : ""}
              {` · ${gapPhrase(c.gapHours)}`}
            </p>
            {(
              [
                [pair.source_repo, c.source],
                [pair.target_repo, c.target],
              ] as const
            ).map(([repo, commit]) => (
              <p key={repo} className="mt-1 flex min-w-0 gap-2 text-xs">
                <span className="shrink-0 font-mono text-[var(--color-text-tertiary)]">
                  {repo} {commit.sha.slice(0, 8)}
                </span>
                <span className="min-w-0 truncate text-[var(--color-text-secondary)]" title={commit.message ?? undefined}>
                  {commit.message?.split("\n")[0] ?? ""}
                </span>
              </p>
            ))}
          </li>
        ))}
      </ul>
      <p className="text-xs leading-relaxed text-[var(--color-text-tertiary)]">
        {commits.length > shown.length ? `Showing ${shown.length} of ${commits.length}. ` : ""}
        {basis}
      </p>
    </>
  );
}
