"use client";

import { useState, type ElementType } from "react";
import type {
  ActionHorizonKey,
  NextAction,
  WorkspaceActionsResponse,
  WorkspaceRepoActions,
} from "@repowise-dev/types/actions";

import { AiPromptModal } from "../health/ai-prompt-modal";
import { buildActionPrompt } from "../health/ai-prompts/action-prompt";
import { ActionRow, renderActionTitle } from "../overview/next-actions";
import { OverviewSection } from "../overview/section";
import { Segmented } from "../shared/segmented";

export interface WorkspaceNextActionsProps {
  data: WorkspaceActionsResponse;
  /** Where one repository's action points, given that repository's id. */
  hrefFor: (repoId: string, action: NextAction) => string | null;
  /** The repository's own Overview, where its full list lives. */
  repoHref: (repoId: string) => string;
  /** Where cross-repository contract changes are listed. */
  contractsHref: string;
  LinkComponent?: ElementType | undefined;
}

function work(repo: WorkspaceRepoActions, horizon: ActionHorizonKey): number {
  return repo.horizons[horizon]?.total ?? 0;
}

function plural(n: number, noun: string): string {
  return `${n.toLocaleString()} ${noun}${n === 1 ? "" : "s"}`;
}

/**
 * "Do next" for the whole workspace: the cross-repository actions first, then
 * each repository's lead actions under its name, most urgent repository first.
 * Each repository's rows are the same actions its own Overview shows, trimmed
 * to the two that lead, with a link to the rest.
 */
export function WorkspaceNextActions({
  data,
  hrefFor,
  repoHref,
  contractsHref,
  LinkComponent,
}: WorkspaceNextActionsProps) {
  const [horizon, setHorizon] = useState<ActionHorizonKey>(
    data.repos.some((r) => work(r, "week") > 0) ? "week" : "quarter",
  );
  const [promptFor, setPromptFor] = useState<{ action: NextAction; repo: string } | null>(null);
  const Link = LinkComponent ?? "a";

  const available = data.repos.filter((r) => r.status === "available");
  const withWork = available
    .filter((r) => work(r, horizon) > 0)
    .sort(
      (a, b) =>
        (b.horizons[horizon]?.by_tier.act_now ?? 0) - (a.horizons[horizon]?.by_tier.act_now ?? 0) ||
        work(b, horizon) - work(a, horizon),
    );
  const total = withWork.reduce((n, r) => n + work(r, horizon), 0) + data.cross_repo.length;
  const unavailable = data.repos.filter((r) => r.status === "unavailable");
  const window = horizon === "week" ? "this week" : "this quarter";

  const status =
    total === 0
      ? `Nothing stands out ${window} in ${available.length.toLocaleString()} ${
          available.length === 1 ? "repository" : "repositories"
        }.`
      : `${plural(total, "thing")} worth doing ${window}, across ${plural(
          withWork.length + (data.cross_repo.length ? 1 : 0),
          "place",
        )}.`;

  return (
    <OverviewSection
      title="Do next"
      description={status}
      action={
        <Segmented
          label="Time frame"
          value={horizon}
          options={[
            { value: "week", label: "This week", hint: "What each repository's last 7 days of commits changed" },
            { value: "quarter", label: "This quarter", hint: "What 90 days say is worth planning" },
          ]}
          onChange={setHorizon}
        />
      }
    >
      <div className="flex flex-col gap-5">
        {data.cross_repo.length > 0 && (
          <div className="flex flex-col">
            <h3 className="pb-1 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
              Across repositories
            </h3>
            <ul className="flex flex-col divide-y divide-[var(--color-border-default)]">
              {data.cross_repo.map((c) => (
                <li key={c.kind} className="py-3.5">
                  <p className="text-[15px] font-semibold leading-snug text-[var(--color-text-primary)]">
                    <Link
                      href={contractsHref}
                      className="rounded hover:text-[var(--color-accent-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
                    >
                      {renderActionTitle(c.title)}
                    </Link>
                  </p>
                  <p className="mt-1 max-w-[72ch] text-xs leading-relaxed text-[var(--color-text-secondary)]">
                    {c.impact}
                  </p>
                </li>
              ))}
            </ul>
          </div>
        )}

        {withWork.map((repo) => {
          const h = repo.horizons[horizon];
          const repoId = repo.repo_id!;
          return (
            <div key={repo.alias} className="flex flex-col">
              <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 pb-1">
                <h3 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                  {repo.alias}
                </h3>
                <Link
                  href={repoHref(repoId)}
                  className="rounded text-xs text-[var(--color-accent-primary)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
                >
                  {`All ${work(repo, horizon).toLocaleString()} in ${repo.alias}`}
                </Link>
              </div>
              <ul className="flex flex-col divide-y divide-[var(--color-border-default)]">
                {(h?.actions ?? []).map((action) => (
                  <ActionRow
                    key={action.id}
                    action={action}
                    href={hrefFor(repoId, action)}
                    LinkComponent={LinkComponent}
                    onPrompt={() => setPromptFor({ action, repo: repo.alias })}
                  />
                ))}
              </ul>
            </div>
          );
        })}

        {total === 0 && (
          <p className="text-sm text-[var(--color-text-secondary)]">
            Each repository's own Overview lists what it checked and why nothing came up.
          </p>
        )}

        {unavailable.length > 0 && (
          <p className="text-xs text-[var(--color-text-tertiary)]">
            {`Not included: ${unavailable.map((r) => `${r.alias} (${r.reason.replace(/\.$/, "").toLowerCase()})`).join(", ")}.`}
          </p>
        )}
      </div>

      <AiPromptModal
        open={promptFor !== null}
        onOpenChange={(open) => {
          if (!open) setPromptFor(null);
        }}
        getPrompt={
          promptFor
            ? (flavor) => buildActionPrompt({ action: promptFor.action, flavor, repoName: promptFor.repo })
            : null
        }
        filePath={promptFor?.action.target.path || null}
        title="Agent handoff"
        description="The action, its evidence, and what done looks like. The agent is asked to confirm the facts before changing anything."
      />
    </OverviewSection>
  );
}
