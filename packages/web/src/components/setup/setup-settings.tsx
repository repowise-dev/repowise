"use client";

import { useMemo, type ReactNode } from "react";
import Link from "next/link";
import { useFormatter, useTranslations } from "next-intl";
import { Skeleton, SkeletonRegion } from "@repowise-dev/ui/ui/skeleton";
import { Button } from "@repowise-dev/ui/ui/button";
import { RowPromptAction } from "@repowise-dev/ui/health/row-prompt-action";
import { CopyLine, SettingsRow, SettingsRows } from "@repowise-dev/ui/settings";
import { hostedLink } from "@/lib/hosted";
import type { RepoResponse } from "@/lib/api/types";
import { SETTINGS_LINK, repoSettingsHref } from "@/components/settings/settings-groups";
import { StatusDot, useSetupText } from "./setup-row";
import { agentListRows, type SetupRow } from "./setup-rows";
import { useSetupControls } from "./setup-controls";
import { useSetupData, useSetupView } from "./use-setup";

/**
 * One Setup row as a settings row: what it is, its status and one line of
 * detail on the left; its control and the agent prompt on the right.
 */
function SetupSettingRow({
  row,
  action,
  onOpenPrompt,
}: {
  row: SetupRow;
  action: ReactNode;
  onOpenPrompt: () => void;
}) {
  const text = useSetupText();
  const title = text(row.title);
  return (
    <SettingsRow
      className="group/row"
      label={title}
      hint={
        <>
          <span className="mr-1.5 inline-flex items-center gap-1.5 text-[var(--color-text-secondary)]">
            <StatusDot tone={row.tone} />
            {text(row.status)}
          </span>
          {text(row.detail)}
        </>
      }
    >
      <div className="flex flex-wrap items-center gap-2 sm:justify-end">
        {action}
        <RowPromptAction onClick={onOpenPrompt} subject={title} />
      </div>
    </SettingsRow>
  );
}

/** Settings rows built from the Setup payload, with the Setup controls. */
function SetupRowsBlock({ repoId, rows }: { repoId: string; rows: SetupRow[] }) {
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
      <SkeletonRegion label={t("loading")} className="flex flex-col gap-2">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
      </SkeletonRegion>
    );
  }
  return (
    <>
      {rows.map((row) => (
        <SetupSettingRow
          key={row.id}
          row={row}
          action={controls.actionFor(row)}
          onOpenPrompt={() => controls.openRowPrompt(row)}
        />
      ))}
      {controls.dialogs}
    </>
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
  const { data } = useSetupData(repoId);
  const view = useSetupView(repoId, repoName, data);
  const rows = useMemo(() => {
    const all = view ? [...view.attention, ...view.essentials, ...view.optional] : [];
    return ids.flatMap((id) => all.filter((r) => r.id === id));
  }, [view, ids]);
  return <SetupRowsBlock repoId={repoId} rows={rows} />;
}

/** "Agents & editors" for one repo: one row per agent, then hosted. */
export function RepoAgentRows({ repoId, repoName }: { repoId: string; repoName: string }) {
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
    <SettingsRows>
      <SetupRowsBlock repoId={repoId} rows={rows} />
      <HostedAgentsRow />
    </SettingsRows>
  );
}

/** The one place local MCP can't reach: the web apps, through hosted repowise. */
export function HostedAgentsRow() {
  const t = useTranslations("setup.row.agents");
  return (
    <SettingsRow label={t("hostedTitle")} hint={t("hostedDetail")}>
      <div className="flex sm:justify-end">
        <a href={hostedLink("settings_agents", "mcp")} target="_blank" rel="noopener noreferrer" className={SETTINGS_LINK}>
          {t("hostedLink")}
        </a>
      </div>
    </SettingsRow>
  );
}

/**
 * Indexed repositories, each with a link into its own settings: global
 * settings lists them where an option is set per repository.
 */
function RepoLinkRows({
  repos,
  hrefFor,
  linkLabel,
  linkAria,
}: {
  repos: RepoResponse[];
  hrefFor: (repoId: string) => string;
  linkLabel: string;
  linkAria: (repo: string) => string;
}) {
  const t = useTranslations("setup.settings");
  const indexed = repos.filter((r) => !r.id.startsWith("ws:") && r.workspace_status !== "needs_index");
  if (indexed.length === 0) {
    return <p className="text-xs text-[var(--color-text-tertiary)]">{t("noRepos")}</p>;
  }
  return (
    <ul className="flex flex-col">
      {indexed.map((repo) => (
        <li
          key={repo.id}
          className="flex items-center justify-between gap-3 border-b border-[var(--color-border-default)] py-2.5 last:border-b-0"
        >
          <span className="min-w-0 truncate text-sm text-[var(--color-text-primary)]">{repo.name}</span>
          <Link href={hrefFor(repo.id)} className={SETTINGS_LINK} aria-label={linkAria(repo.name)}>
            {linkLabel}
          </Link>
        </li>
      ))}
    </ul>
  );
}

/** Global Setup: each repository's Setup page. */
export function SetupRepoLinks({ repos }: { repos: RepoResponse[] }) {
  const t = useTranslations("setup.settings");
  return (
    <RepoLinkRows
      repos={repos}
      hrefFor={(id) => repoSettingsHref(id, "setup")}
      linkLabel={t("openSetup")}
      linkAria={(repo) => t("openSetupFor", { repo })}
    />
  );
}

/** Global Agents: MCP tools are chosen in each repository's Agents settings. */
export function McpToolsRepoLinks({ repos }: { repos: RepoResponse[] }) {
  const t = useTranslations("settings.mcpTools");
  return (
    <RepoLinkRows
      repos={repos}
      hrefFor={(id) => repoSettingsHref(id, "agents")}
      linkLabel={t("chooseTools")}
      linkAria={(repo) => t("chooseToolsFor", { repo })}
    />
  );
}

/**
 * Privacy holds the telemetry status. The dashboard has no switch for it, so
 * it says how to check and change it from the terminal.
 */
export function TelemetryRows() {
  const t = useTranslations("setup.settings");
  return (
    <SettingsRows>
      <SettingsRow label={t("telemetryCheckLabel")}>
        <CopyLine value="repowise telemetry status" copyLabel={t("telemetryCheckLabel")} />
      </SettingsRow>
      <SettingsRow label={t("telemetryOffLabel")} hint={t("telemetryOffHint")}>
        <CopyLine value="repowise telemetry disable" copyLabel={t("telemetryOffLabel")} />
      </SettingsRow>
    </SettingsRows>
  );
}
