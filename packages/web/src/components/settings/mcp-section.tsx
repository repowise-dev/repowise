"use client";

import { useState } from "react";
import useSWR from "swr";
import { McpClientConfig, SettingsRow, type McpClientConfigLabels } from "@repowise-dev/ui/settings";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { useTranslations } from "next-intl";
import { getMcpClientConfig } from "@/lib/api/mcp-tools";

/**
 * The MCP config to paste into one agent, for one repository, as
 * `repowise agents print-config` prints it, so each agent gets its own shape.
 */
export function McpSection({ repos }: { repos: { id: string; name: string }[] }) {
  const t = useTranslations("settings");
  const [repoId, setRepoId] = useState<string | null>(repos[0]?.id ?? null);
  const [target, setTarget] = useState<string | null>(null);
  const { data, error, isLoading } = useSWR(
    repoId ? ["mcp-client-config", repoId, target] : null,
    () => getMcpClientConfig(repoId as string, target ?? undefined),
    // The last snippet stays up while the next loads, so the page does not jump.
    { keepPreviousData: true, revalidateOnFocus: false },
  );

  const labels: Partial<McpClientConfigLabels> = {
    agent: t("mcp.agent"),
    repo: t("mcp.repo"),
    snippet: (agent) => t("mcp.snippet", { agent }),
    surface: (agent, count, profile) =>
      profile ? t("mcp.surface", { agent, count, profile }) : t("mcp.surfaceExact", { agent, count }),
    alwaysLoadOn: t("mcp.alwaysLoadOn"),
    alwaysLoadOff: t("mcp.alwaysLoadOff"),
    docs: (agent) => t("mcp.docs", { agent }),
    loading: t("mcp.loading"),
    noRepos: t("mcp.noRepos"),
  };

  return (
    <SettingsRow label={t("mcp.serverLabel")} hint={t("mcp.serverHint")}>
      <McpClientConfig
        repos={repos}
        repoId={repoId}
        onRepoChange={setRepoId}
        target={target}
        onTargetChange={setTarget}
        // A snippet carries its repository's path: never show one for another repository.
        data={data && data.repo_id === repoId ? data : null}
        loading={isLoading}
        error={error ? toFriendlyMessage(error, t("mcp.loadFailed")) : null}
        labels={labels}
      />
    </SettingsRow>
  );
}
