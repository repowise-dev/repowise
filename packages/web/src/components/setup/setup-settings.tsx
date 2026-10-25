"use client";

import { useMemo } from "react";
import Link from "next/link";
import { useFormatter, useTranslations } from "next-intl";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { Skeleton, SkeletonRegion } from "@repowise-dev/ui/ui/skeleton";
import { Button } from "@repowise-dev/ui/ui/button";
import { hostedLink } from "@/lib/hosted";
import type { RepoResponse } from "@/lib/api/types";
import { SetupCommand, SetupRowView, StatusDot } from "./setup-row";
import { agentListRows, type SetupRow } from "./setup-rows";
import { useSetupControls } from "./setup-controls";
import { setupHref } from "./setup-pill";
import { useSetupData, useSetupView } from "./use-setup";

const LINK =
  "text-xs font-medium text-[var(--color-text-primary)] underline decoration-[var(--color-border-hover)] underline-offset-[3px] hover:decoration-[var(--color-text-primary)]";

const ROW_GRID =
  "grid grid-cols-1 gap-x-4 gap-y-2 border-t border-[var(--color-border-default)] py-3 sm:grid-cols-[124px_minmax(0,1fr)_auto]";

/** Settings rows built from the Setup payload, with the Setup page's own controls. */
function SetupRowsBlock({
  repoId,
  label,
  rows,
}: {
  repoId: string;
  label: string;
  rows: SetupRow[];
}) {
  const t = useTranslations("setup");
  const controls = useSetupControls(repoId);
  const { data, error, isLoading, mutate } = useSetupData(repoId);
  if (!data && error && !isLoading) {
    return (
      <p className="flex flex-wrap items-center gap-2 text-xs text-[var(--color-text-secondary)]">
        {t("loadFailed")}
        <Button size="sm" variant="outline" onClick={() => void mutate()}>
          {t("retry")}
        </Button>
      </p>
    );
  }
  if (!data) {
    return (
      <SkeletonRegion label={label} className="flex flex-col gap-2">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
      </SkeletonRegion>
    );
  }
  return (
    <div>
      {rows.map((row) => (
        <SetupRowView
          key={row.id}
          row={row}
          action={controls.actionFor(row)}
          onOpenPrompt={() => controls.openRowPrompt(row)}
        />
      ))}
      {controls.dialogs}
    </div>
  );
}

/** Rows from the Setup page, by id: e.g. `["sync", "rewrite"]` for Sync & hooks. */
export function RepoSetupRows({
  repoId,
  repoName,
  ids,
}: {
  repoId: string;
  repoName: string;
  ids: string[];
}) {
  const t = useTranslations("setup");
  const { data } = useSetupData(repoId);
  const view = useSetupView(repoId, repoName, data);
  const rows = useMemo(() => {
    const all = view ? [...view.attention, ...view.essentials, ...view.optional] : [];
    return ids.flatMap((id) => all.filter((r) => r.id === id));
  }, [view, ids]);
  return <SetupRowsBlock repoId={repoId} label={t("loading")} rows={rows} />;
}

/** "Agents & editors" for one repo: the Setup page's agent rows, then hosted. */
export function RepoAgentRows({ repoId, repoName }: { repoId: string; repoName: string }) {
  const t = useTranslations("setup");
  const format = useFormatter();
  const { data } = useSetupData(repoId);
  const rows = useMemo(() => {
    if (!data) return [];
    const now = Date.now();
    return agentListRows(data, {
      repoName,
      now,
      relative: (iso) => format.relativeTime(new Date(iso), now),
    });
  }, [data, repoName, format]);
  return (
    <div>
      <SetupRowsBlock repoId={repoId} label={t("loading")} rows={rows} />
      <HostedAgentsRow />
    </div>
  );
}

/** The one place local MCP can't reach: the web apps, through hosted repowise. */
export function HostedAgentsRow() {
  const t = useTranslations("setup.row.agents");
  return (
    <div className={ROW_GRID}>
      <span className="flex items-center gap-1.5 pt-px text-xs text-[var(--color-text-secondary)]">
        <StatusDot tone="found" />
        {t("statusHosted")}
      </span>
      <div className="min-w-0">
        <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">{t("hostedTitle")}</h3>
        <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">{t("hostedDetail")}</p>
      </div>
      <div className="flex items-center sm:justify-end">
        <a
          href={hostedLink("settings_agents", "mcp")}
          target="_blank"
          rel="noopener noreferrer"
          className={LINK}
        >
          {t("hostedLink")}
        </a>
      </div>
    </div>
  );
}

/** Repo settings' Setup group lead: where the full checklist lives. */
export function SetupLinkRow({ repoId }: { repoId: string }) {
  const t = useTranslations("setup.settings");
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <p className="text-xs text-[var(--color-text-secondary)]">{t("repoLead")}</p>
      <Link href={setupHref(repoId)} className={LINK}>
        {t("openSetup")}
      </Link>
    </div>
  );
}

/** Global settings' Setup group: Setup is per repository, so link to each. */
export function GlobalSetupLinks({ repos }: { repos: RepoResponse[] }) {
  const t = useTranslations("setup.settings");
  const indexed = repos.filter((r) => !r.id.startsWith("ws:") && r.workspace_status !== "needs_index");
  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs text-[var(--color-text-secondary)]">{t("globalLead")}</p>
      {indexed.length === 0 ? (
        <p className="text-xs text-[var(--color-text-tertiary)]">{t("noRepos")}</p>
      ) : (
        <ul className="flex flex-col">
          {indexed.map((repo) => (
            <li
              key={repo.id}
              className="flex items-center justify-between gap-3 border-t border-[var(--color-border-default)] py-2"
            >
              <span className="min-w-0 truncate text-sm text-[var(--color-text-primary)]">{repo.name}</span>
              <Link href={setupHref(repo.id)} className={LINK} aria-label={t("openSetupFor", { repo: repo.name })}>
                {t("openSetup")}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * Privacy holds the telemetry status. The dashboard has no switch for it, so
 * it says how to check and change it from the terminal.
 */
export function TelemetryRow() {
  const t = useTranslations("setup.settings");
  return (
    <OverviewSection title={t("telemetryTitle")} description={t("telemetryDescription")}>
      <div className="flex flex-col gap-1.5 text-xs text-[var(--color-text-secondary)]">
        <span className="flex flex-wrap items-center gap-2">
          {t("telemetryCheck")} <SetupCommand command="repowise telemetry status" />
        </span>
        <span className="flex flex-wrap items-center gap-2">
          {t("telemetryOff")} <SetupCommand command="repowise telemetry disable" />
        </span>
      </div>
    </OverviewSection>
  );
}
