"use client";

import Link from "next/link";
import useSWR from "swr";
import { useTranslations } from "next-intl";
import { Button } from "@repowise-dev/ui/ui/button";
import { Callout } from "@repowise-dev/ui/shared/callout";
import { Skeleton, SkeletonRegion } from "@repowise-dev/ui/ui/skeleton";
import { DecisionAgentHero } from "@repowise-dev/ui/decisions/decision-agent-prompts";
import { getWorkspaceSetup, type WorkspaceSetupRepo } from "@/lib/api/workspace";
import { StatusDot } from "./setup-row";
import type { SetupTone } from "./setup-rows";

/** The aggregate's SWR key. */
export const WORKSPACE_SETUP_KEY = ["workspace-setup", "page"] as const;

type Cell = { tone: SetupTone; key: string };

const NA: Cell = { tone: "off", key: "na" };

/** One member's cells, as words and dots; only what needs a look is marked. */
export function workspaceSetupCells(r: WorkspaceSetupRepo): Record<
  "index" | "agent" | "sync" | "coverage" | "evidence",
  Cell
> {
  if (r.status !== "available") {
    const index: Cell =
      r.status === "not_indexed" ? { tone: "attention", key: "notIndexed" } : { tone: "off", key: "unreadable" };
    return { index, agent: NA, sync: NA, coverage: NA, evidence: NA };
  }
  const index: Cell =
    r.index_state === "up_to_date"
      ? { tone: "done", key: "upToDate" }
      : r.index_state === "behind"
        ? { tone: "attention", key: "behind" }
        : r.index_state === "incomplete"
          ? { tone: "attention", key: "incomplete" }
          : { tone: "off", key: "unreadable" };
  const agent: Cell =
    r.first_call_at || (r.agent_calls ?? 0) > 0
      ? { tone: "done", key: "connected" }
      : r.agent_calls == null
        ? { tone: "off", key: "unreadable" }
        : { tone: "waiting", key: "noCalls" };
  const sync: Cell =
    r.sync == null ? { tone: "off", key: "unreadable" } : r.sync ? { tone: "done", key: "on" } : { tone: "attention", key: "off" };
  const signal = (v: WorkspaceSetupRepo["coverage"]): Cell =>
    v === "on"
      ? { tone: "done", key: "on" }
      : v === "found"
        ? { tone: "found", key: "found" }
        : v === "none"
          ? { tone: "off", key: "none" }
          : { tone: "off", key: "unreadable" };
  return { index, agent, sync, coverage: signal(r.coverage), evidence: signal(r.evidence) };
}

const COLUMNS = ["index", "agent", "sync", "coverage", "evidence"] as const;

/**
 * Setup across a workspace: one row per member, and while any member has
 * steps left after its first agent call, the workspace form of the
 * finish-setup prompt. Each member's own Setup holds its rows and prompts.
 */
export function WorkspaceSetup() {
  const t = useTranslations("setup.workspace");
  const { data, error, isLoading, mutate } = useSWR(WORKSPACE_SETUP_KEY, () => getWorkspaceSetup(), {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  });

  if (!data) {
    if (error && !isLoading) {
      return (
        <Callout
          tone="error"
          label={t("loadFailedLabel")}
          action={
            <Button size="sm" variant="outline" onClick={() => void mutate()}>
              {t("retry")}
            </Button>
          }
        >
          {t("loadFailed")}
        </Callout>
      );
    }
    return (
      <SkeletonRegion label={t("loading")} className="flex flex-col gap-2">
        {Array.from({ length: 4 }, (_, i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </SkeletonRegion>
    );
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-[var(--color-text-secondary)]">
        {t("progress", { done: data.essentials_done, total: data.repos.length })}
      </p>
      {data.offer === "finish_setup" && data.prompt && (
        <DecisionAgentHero
          prompt={{ kind: "finish_setup", text: data.prompt, count: data.open }}
          eyebrow={t("heroEyebrow")}
          heading={t("heroTitle")}
          description={t("heroBody")}
          copyLabel={t("heroCopy")}
          copiedLabel={t("heroCopied")}
          failedLabel={t("heroCopyFailed")}
          previewLines={3}
        />
      )}
      <WorkspaceSetupTable repos={data.repos} />
      <p className="text-xs text-[var(--color-text-tertiary)]">{t("foot")}</p>
    </div>
  );
}

function WorkspaceSetupTable({ repos }: { repos: WorkspaceSetupRepo[] }) {
  const t = useTranslations("setup.workspace");
  return (
    <div className="overflow-x-auto rounded-lg border border-[var(--color-border-default)]">
      <table className="w-full min-w-[560px] text-sm">
        <thead>
          <tr className="text-left">
            <th scope="col" className="px-3 py-2 font-mono text-[10px] font-normal uppercase tracking-[0.1em] text-[var(--color-text-tertiary)]">
              {t("col.repo")}
            </th>
            {COLUMNS.map((c) => (
              <th
                key={c}
                scope="col"
                className="px-3 py-2 font-mono text-[10px] font-normal uppercase tracking-[0.1em] text-[var(--color-text-tertiary)]"
              >
                {t(`col.${c}`)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {repos.map((r) => {
            const cells = workspaceSetupCells(r);
            return (
              <tr key={r.alias} className="border-t border-[var(--color-border-default)]">
                <th scope="row" className="px-3 py-2.5 text-left font-medium text-[var(--color-text-primary)]">
                  {r.repo_id ? (
                    <Link
                      href={`/repos/${encodeURIComponent(r.repo_id)}/settings/setup`}
                      className="underline decoration-[var(--color-border-hover)] underline-offset-[3px] hover:decoration-[var(--color-text-primary)]"
                    >
                      {r.alias}
                    </Link>
                  ) : (
                    r.alias
                  )}
                </th>
                {COLUMNS.map((c) => (
                  <td key={c} className="whitespace-nowrap px-3 py-2.5 text-[var(--color-text-secondary)]">
                    <span className="inline-flex items-center gap-1.5">
                      <StatusDot tone={cells[c].tone} />
                      {t(`cell.${cells[c].key}`)}
                    </span>
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
